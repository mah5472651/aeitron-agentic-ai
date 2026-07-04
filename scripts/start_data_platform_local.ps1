param(
    [int]$WorkerScale = 4
)

$ErrorActionPreference = "Stop"

if (-not $env:POSTGRES_PASSWORD) {
    $env:POSTGRES_PASSWORD = "mythos-local-postgres-change-me"
}
if (-not $env:MINIO_ROOT_USER) {
    $env:MINIO_ROOT_USER = "mythosminio"
}
if (-not $env:MINIO_ROOT_PASSWORD) {
    $env:MINIO_ROOT_PASSWORD = "mythos-local-minio-change-me-123456"
}
if (-not $env:MYTHOS_JWT_SECRET) {
    $env:MYTHOS_JWT_SECRET = "local-development-jwt-secret-change-before-production-000000"
}

docker compose -f deploy/prod/docker-compose.yml --profile data up -d postgres redis minio
docker compose -f deploy/prod/docker-compose.yml --profile data up -d --scale crawler-worker=$WorkerScale crawler-worker

Write-Host "Data platform started."
Write-Host "Postgres: postgresql://mythos:$($env:POSTGRES_PASSWORD)@localhost:5432/mythos"
Write-Host "MinIO console: http://127.0.0.1:9001"
