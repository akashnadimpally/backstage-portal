param(
  [switch]$Docker
)

$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
Set-Location $repoRoot

function Stop-WithMessage {
  param([string]$Message)
  Write-Error $Message
  exit 1
}

if (-not (Get-Command yarn -ErrorAction SilentlyContinue)) {
  Stop-WithMessage 'Yarn was not found. Install the project package manager, then rerun this script.'
}

$envFile = Join-Path $repoRoot '.env'
if (-not (Test-Path $envFile)) {
  $envExample = Join-Path $repoRoot '.env.example'
  if (-not (Test-Path $envExample)) {
    Stop-WithMessage 'Neither .env nor .env.example exists in the repository root.'
  }
  Copy-Item $envExample $envFile
  Stop-WithMessage 'Created .env from .env.example. Set POSTGRES_PASSWORD to match PostgreSQL and LOCAL_AUTH_ADMIN_PASSWORD to a unique secret of at least 12 characters, then rerun this script.'
}

$adminPasswordConfigured = Select-String -Path $envFile -Pattern '^LOCAL_AUTH_ADMIN_PASSWORD=(?:"[^"\r\n]{12,}"|[^"\s#][^\r\n]{11,})$' -Quiet
if (-not $adminPasswordConfigured) {
  Stop-WithMessage 'Set LOCAL_AUTH_ADMIN_PASSWORD in .env to a unique secret of at least 12 characters, then rerun this script.'
}

if (-not (Test-Path (Join-Path $repoRoot 'node_modules'))) {
  Write-Host 'Installing dependencies...'
  yarn install --immutable
  if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage 'Dependency installation failed.'
  }
}

if ($Docker) {
  if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-WithMessage 'Docker was not found. Install/start Docker Desktop, then rerun with -Docker.'
  }

  $composeFile = Join-Path $repoRoot 'docker-compose.windows.yml'
  if (-not (Test-Path $composeFile)) {
    Stop-WithMessage 'docker-compose.windows.yml was not found.'
  }

  Write-Host 'Starting the complete Backstage stack with Docker Compose...'
  docker compose -f $composeFile up --build -d
  if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage 'Docker Compose failed to start the Backstage stack.'
  }

  Write-Host ''
  Write-Host 'Backstage is starting:'
  Write-Host '  Frontend: http://localhost:3000'
  Write-Host '  Backend:  http://localhost:7007'
  Write-Host '  Health:   http://localhost:7007/healthcheck'
  Write-Host ''
  Write-Host 'Follow logs with: docker compose -f docker-compose.windows.yml logs -f'
  Write-Host 'Stop the stack with: docker compose -f docker-compose.windows.yml down'
  exit 0
}

$ports = @(3000, 7007)
$listeners = @(
  Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $ports -contains $_.LocalPort }
)
if ($listeners.Count -gt 0) {
  $busyPorts = ($listeners |
    Select-Object -ExpandProperty LocalPort -Unique |
    Sort-Object |
    ForEach-Object { "$($_)" }) -join ', '
  Stop-WithMessage "Port(s) $busyPorts are already in use. Stop the existing process or choose different app ports before rerunning."
}

if (-not (Test-NetConnection -ComputerName localhost -Port 5432 -InformationLevel Quiet)) {
  Stop-WithMessage 'PostgreSQL is not reachable at localhost:5432. Start your configured database, or rerun with -Docker to start the complete Docker Compose stack.'
}

Write-Host 'Starting Backstage frontend and backend together...'
Write-Host '  Frontend: http://localhost:3000'
Write-Host '  Backend:  http://localhost:7007'
Write-Host ''

yarn start
if ($LASTEXITCODE -ne 0) {
  Stop-WithMessage 'Backstage exited with an error.'
}
