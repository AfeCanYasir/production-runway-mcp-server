$ErrorActionPreference = "Stop"
if (-not $env:RUNWAYML_API_SECRET) {
  $env:RUNWAYML_API_SECRET = Read-Host "Enter RUNWAYML_API_SECRET"
}
docker compose up --build -d
Write-Host "Runway MCP is available at http://localhost:8000/mcp"
