#!/usr/bin/env bash
set -euo pipefail
: "${RUNWAYML_API_SECRET:?Set RUNWAYML_API_SECRET first}"
docker compose up --build -d
echo "Runway MCP is available at http://localhost:8000/mcp"
