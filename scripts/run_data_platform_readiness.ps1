param(
    [string]$Sources = "config/data_sources.production.sample.json",
    [string]$DatabaseUrl = $env:AEITRON_DATABASE_URL,
    [string]$ObjectStoreUri = "s3://aeitron-datasets/pretraining",
    [int]$WorkerReplicas = 8,
    [int]$AsyncWorkers = 64
)

$ErrorActionPreference = "Stop"

if (-not $DatabaseUrl) {
    throw "AEITRON_DATABASE_URL or -DatabaseUrl is required"
}

python -m src.aeitron.learning.production_check `
    --sources $Sources `
    --frontier-backend postgres `
    --postgres-dsn $DatabaseUrl `
    --object-store-uri $ObjectStoreUri `
    --production `
    --worker-replicas $WorkerReplicas `
    --async-workers $AsyncWorkers

