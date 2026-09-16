# Production Runway MCP Server
*mixed · 2 files · last updated 2026-09-16T08:53:38+00:00*

Production-hardened FastMCP server for Runway text-to-video generation with durable task tracking, validation, retries, idempotency, structured errors, health diagnostics, cancellation, and secure configuration.

## Files
- `requirements.txt` (1 KB)
- `server.py` (11 KB)

## Provenance
**Conversation: 251ff0ef-ca02-4650-be72-d57d5854ee66**
- Turn 1 — Created artifact: Production Runway MCP Server
- Turn 2 — Opened artifact: Production Runway MCP Server

## Run with Docker

PowerShell:

```powershell
$env:RUNWAYML_API_SECRET = "your-key"
.\start.ps1
```

Linux/macOS:

```bash
export RUNWAYML_API_SECRET="your-key"
./start.sh
```

The n8n MCP endpoint is `http://localhost:8000/mcp` when n8n runs on the same machine. For remote n8n, deploy this container behind HTTPS and use the public `/mcp` URL. Do not expose it directly to the internet without an authentication proxy or private network.

The server supports `MCP_TRANSPORT=stdio` for local MCP clients and `MCP_TRANSPORT=streamable-http` for n8n.
