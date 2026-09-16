$ErrorActionPreference = "Stop"

# Always run Compose from the folder containing this script.
Set-Location -LiteralPath $PSScriptRoot

try {
  docker info *> $null
  if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running. Start Docker Desktop and run this script again."
  }
} catch {
  Write-Error $_.Exception.Message
  exit 1
}

if (-not $env:RUNWAYML_API_SECRET) {
  $env:RUNWAYML_API_SECRET = Read-Host "Enter RUNWAYML_API_SECRET"
}
if ([string]::IsNullOrWhiteSpace($env:RUNWAYML_API_SECRET)) {
  throw "RUNWAYML_API_SECRET cannot be empty."
}

docker compose up --build -d
if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed to start the service." }

Start-Sleep -Seconds 3
docker compose ps
Write-Host ""
Write-Host "Runway MCP is available at http://localhost:8000/mcp" -ForegroundColor Green
Write-Host "Use this URL in n8n with Streamable HTTP." -ForegroundColor Green
