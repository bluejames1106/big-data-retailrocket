param([string]$SourceRun='', [string]$RunId=('hybrid-'+(Get-Date -Format 'yyyyMMdd-HHmmss')))
$ErrorActionPreference='Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    if(!$SourceRun){
        $cursor=(Get-Content -LiteralPath 'outputs/latest_run.json' -Raw | ConvertFrom-Json).run_id
        for($depth=0;$depth -lt 64;$depth++){
            if(Test-Path -LiteralPath "outputs/runs/$cursor/models/evaluation_pipeline"){$SourceRun=$cursor;break}
            $sourceReport=Get-Content -LiteralPath "outputs/runs/$cursor/metrics/run_report.json" -Raw | ConvertFrom-Json
            $cursor=$sourceReport.source_run_id
            if(!$cursor){throw 'No existing full ALS model found; run scripts/run_full_training.ps1 first'}
        }
        if(!$SourceRun){throw 'Source lineage exceeds limit'}
    }
    docker build -t retailrocket-phase17:1.0 .
    if($LASTEXITCODE -ne 0){throw 'Image build failed'}
    docker compose up -d --wait namenode datanode mongodb redis
    if($LASTEXITCODE -ne 0){throw 'Infrastructure startup failed'}
    # The compatible API can keep serving the previous model during training.
    docker compose up -d --no-deps --force-recreate --wait api
    if($LASTEXITCODE -ne 0){throw 'API startup failed'}
    docker compose run --rm --no-deps app python -m pipeline.hybrid_upgrade --source-run $SourceRun --run-id $RunId
    if($LASTEXITCODE -ne 0){throw 'Upgrade failed; inspect run_report.json; previous model remains selected'}
    docker compose run --rm --no-deps app python scripts/verify_full_training.py --run-id $RunId
    if($LASTEXITCODE -ne 0){throw 'Model/API verification failed'}
    docker compose run --rm --no-deps app python -m scripts.benchmark --run-id $RunId --requests 100 --concurrency 4
    if($LASTEXITCODE -ne 0){throw 'API benchmark failed'}
    Write-Host "Upgrade completed: $RunId | http://localhost:8000"
} finally {Pop-Location}
