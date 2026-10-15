param([switch]$Build, [switch]$Open)
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env; add your OPENROUTER_API_KEY." }
docker image inspect finally *> $null
if ($Build -or $LASTEXITCODE -ne 0) { docker build -t finally . }
docker rm -f finally *> $null
docker run -d --name finally -v finally-data:/app/db -p 8000:8000 --env-file .env finally | Out-Null
Write-Host "FinAlly running at http://localhost:8000"
if ($Open) { Start-Process "http://localhost:8000" }
