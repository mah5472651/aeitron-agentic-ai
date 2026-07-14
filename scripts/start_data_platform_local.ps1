param(
    [int]$WorkerScale = 4
)

$ErrorActionPreference = "Stop"

if (-not $env:POSTGRES_PASSWORD) {
    $env:POSTGRES_PASSWORD = "aeitron-local-postgres-change-me"
}
if (-not $env:MINIO_ROOT_USER) {
    $env:MINIO_ROOT_USER = "aeitronminio"
}
if (-not $env:MINIO_ROOT_PASSWORD) {
    $env:MINIO_ROOT_PASSWORD = "aeitron-local-minio-change-me-123456"
}
if (-not $env:AEITRON_JWT_SECRET) {
    $env:AEITRON_JWT_SECRET = "local-development-jwt-secret-change-before-production-000000"
}

docker compose -f deploy/prod/docker-compose.yml --profile data up -d postgres redis minio
docker compose -f deploy/prod/docker-compose.yml --profile data up -d --scale crawler-worker=$WorkerScale crawler-worker

Write-Host "Data platform started."
Write-Host "Postgres: postgresql://aeitron:$($env:POSTGRES_PASSWORD)@localhost:5432/aeitron"
Write-Host "MinIO console: http://127.0.0.1:9001"

