"""Production Runway text-to-video MCP server.

Run:
  pip install -r requirements.txt
  set RUNWAYML_API_SECRET=...
  python server.py

The server deliberately uses Runway's current text_to_video endpoint. The older
image_to_video call is not a text-only endpoint and was the main correctness bug
in the original implementation.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from mcp.server.fastmcp import FastMCP
from runwayml import RunwayML

APP_NAME = "Enterprise-Video-Generator"
DB_FILE = os.getenv("VIDEO_TASKS_DB", "video_tasks.db")
MAX_PROMPT_UTF16 = 1000
VALID_RATIOS = {"16:9": "1280:720", "9:16": "720:1280"}
VALID_DURATIONS = {2, 3, 4, 5, 6, 7, 8, 9, 10}
TERMINAL = {"completed", "failed", "cancelled"}
logger = logging.getLogger(APP_NAME)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
mcp = FastMCP(APP_NAME)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utf16_len(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def validate_prompt(prompt: str) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt must not be empty")
    if utf16_len(prompt) > MAX_PROMPT_UTF16:
        raise ValueError(f"prompt must be at most {MAX_PROMPT_UTF16} UTF-16 code units")
    return prompt


def api_key() -> str:
    value = os.getenv("RUNWAYML_API_SECRET", "").strip()
    if not value:
        raise RuntimeError("RUNWAYML_API_SECRET is not configured")
    return value


def client() -> RunwayML:
    # The SDK also supports reading RUNWAYML_API_SECRET itself, but passing it
    # explicitly makes configuration failures deterministic and testable.
    return RunwayML(api_key=api_key(), max_retries=2, timeout=60.0)


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
          internal_id TEXT PRIMARY KEY,
          external_id TEXT NOT NULL UNIQUE,
          idempotency_key TEXT UNIQUE,
          status TEXT NOT NULL CHECK(status IN ('queued','processing','completed','failed','cancelled')),
          prompt TEXT NOT NULL,
          aspect_ratio TEXT NOT NULL,
          duration INTEGER NOT NULL,
          output_format TEXT NOT NULL,
          video_url TEXT,
          error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          completed_at TEXT
        )""")
        # Safe additive migrations for databases created by the original server.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        additions = {
          "idempotency_key": "TEXT",
          "duration": "INTEGER NOT NULL DEFAULT 5",
          "output_format": "TEXT NOT NULL DEFAULT 'mp4'",
          "error": "TEXT",
          "created_at": "TEXT NOT NULL DEFAULT ''",
          "updated_at": "TEXT NOT NULL DEFAULT ''",
          "completed_at": "TEXT",
        }
        for name, typ in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {typ}")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL")


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row) if row else {}


def normalize_remote_status(status: str) -> str:
    s = (status or "").upper()
    return {"SUCCEEDED": "completed", "FAILED": "failed", "CANCELLED": "cancelled", "ABORTED": "cancelled"}.get(s, "processing")


def extract_output(task: Any) -> Optional[str]:
    output = getattr(task, "output", None)
    if isinstance(output, (list, tuple)) and output:
        return str(output[0])
    if isinstance(output, str):
        return output
    return None


def public_task(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    d.pop("idempotency_key", None)
    return {k: d.get(k) for k in ("internal_id", "status", "prompt", "aspect_ratio", "duration", "output_format", "video_url", "error", "created_at", "updated_at", "completed_at")}


@mcp.tool()
def generate_video(prompt: str, aspect_ratio: str = "16:9", duration: int = 5, idempotency_key: str = "") -> dict[str, Any]:
    """Queue a Runway Gen-4.5 text-to-video task and return a stable internal ID.

    Repeating the same idempotency_key returns the original task instead of
    spending credits twice. Poll with check_video_status.
    """
    try:
        prompt = validate_prompt(prompt)
        if aspect_ratio not in VALID_RATIOS:
            raise ValueError(f"aspect_ratio must be one of {sorted(VALID_RATIOS)}")
        if duration not in VALID_DURATIONS:
            raise ValueError("duration must be an integer from 2 through 10")
        idem = idempotency_key.strip() or None
        if idem and len(idem) > 200:
            raise ValueError("idempotency_key must be 200 characters or fewer")

        init_db()
        if idem:
            with db() as conn:
                existing = conn.execute("SELECT * FROM tasks WHERE idempotency_key=?", (idem,)).fetchone()
            if existing:
                return {"ok": True, "reused": True, "task": public_task(existing)}

        # Runway currently supports text-to-video through Gen-4.5. gen3a_turbo
        # belongs to image-to-video, so using it here would be a production bug.
        remote = None
        last_error = None
        for attempt in range(3):
            try:
                remote = client().text_to_video.create(
                    model="gen4.5", prompt_text=prompt,
                    ratio=VALID_RATIOS[aspect_ratio], duration=duration,
                    output_format="mp4",
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
        if remote is None:
            raise RuntimeError(f"Runway submission failed after retries: {last_error}")

        external_id = str(getattr(remote, "id", ""))
        if not external_id:
            raise RuntimeError("Runway returned no task ID")
        now = utc_now()
        internal_id = str(uuid.uuid4())
        with db() as conn:
            conn.execute("""INSERT INTO tasks
              (internal_id, external_id, idempotency_key, status, prompt, aspect_ratio, duration, output_format, created_at, updated_at)
              VALUES (?, ?, ?, 'processing', ?, ?, ?, 'mp4', ?, ?)""",
              (internal_id, external_id, idem, prompt, aspect_ratio, duration, now, now))
            row = conn.execute("SELECT * FROM tasks WHERE internal_id=?", (internal_id,)).fetchone()
        return {"ok": True, "reused": False, "task": public_task(row), "message": "Task submitted; poll check_video_status."}
    except Exception as exc:
        logger.exception("generate_video failed")
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def check_video_status(task_id: str, refresh: bool = True) -> dict[str, Any]:
    """Return local task state, optionally refreshing it from Runway."""
    try:
        task_id = (task_id or "").strip()
        if not task_id:
            return {"ok": False, "error": "task_id is required"}
        init_db()
        with db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE internal_id=?", (task_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "task not found"}
        if not refresh or row["status"] in TERMINAL:
            return {"ok": True, "task": public_task(row)}

        remote = client().tasks.retrieve(id=row["external_id"])
        status = normalize_remote_status(getattr(remote, "status", ""))
        url = extract_output(remote) if status == "completed" else row["video_url"]
        err = str(getattr(remote, "failure", None) or getattr(remote, "error", None) or "") if status == "failed" else None
        now = utc_now()
        with db() as conn:
            conn.execute("UPDATE tasks SET status=?, video_url=?, error=?, updated_at=?, completed_at=? WHERE internal_id=?",
              (status, url, err, now, now if status in TERMINAL else None, task_id))
            updated = conn.execute("SELECT * FROM tasks WHERE internal_id=?", (task_id,)).fetchone()
        return {"ok": True, "task": public_task(updated)}
    except Exception as exc:
        logger.exception("check_video_status failed")
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def cancel_video(task_id: str) -> dict[str, Any]:
    """Cancel a queued/processing task and persist the cancellation."""
    try:
        with db() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE internal_id=?", ((task_id or "").strip(),)).fetchone()
        if not row:
            return {"ok": False, "error": "task not found"}
        if row["status"] in TERMINAL:
            return {"ok": True, "task": public_task(row), "message": "Already terminal; no API call made."}
        client().tasks.delete(id=row["external_id"])
        now = utc_now()
        with db() as conn:
            conn.execute("UPDATE tasks SET status='cancelled', updated_at=?, completed_at=? WHERE internal_id=?", (now, now, task_id))
            updated = conn.execute("SELECT * FROM tasks WHERE internal_id=?", (task_id,)).fetchone()
        return {"ok": True, "task": public_task(updated)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def list_video_tasks(limit: int = 20, status: str = "") -> dict[str, Any]:
    """List recent local tasks for operations and support."""
    try:
        limit = max(1, min(int(limit), 100))
        with db() as conn:
            if status:
                rows = conn.execute("SELECT * FROM tasks WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return {"ok": True, "tasks": [public_task(r) for r in rows]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
def health_check() -> dict[str, Any]:
    """Check configuration and local persistence without spending credits."""
    try:
        init_db()
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"ok": True, "service": APP_NAME, "database": "ok", "runway_key_configured": bool(os.getenv("RUNWAYML_API_SECRET"))}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "runway_key_configured": bool(os.getenv("RUNWAYML_API_SECRET"))}


init_db()

if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    if transport in {"streamable-http", "streamable_http", "http"}:
        # FastMCP reads these settings when run() starts. They are deliberately
        # environment-driven so the same image works on Render, Railway, Docker,
        # or a private VM without source changes.
        mcp.settings.host = os.getenv("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.getenv("PORT", os.getenv("MCP_PORT", "8000")))
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
