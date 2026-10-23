param([int]$Port=8000,[switch]$SkipBuild)
$ErrorActionPreference='Stop'
$savedPort=$env:APP_PORT
Push-Location (Join-Path $PSScriptRoot '..')
try {
    if($Port -lt 1 -or $Port -gt 65535){throw 'Invalid port'}
    $env:APP_PORT=[string]$Port
    $composeArgs=@('compose','-p','retailrocket-update2','-f','compose.yaml','-f','compose.local-update2.yaml')
    if(!$SkipBuild){
        docker build -t bigdata-update2:local .
        if($LASTEXITCODE -ne 0){throw 'Image build failed'}
    }
    docker @composeArgs up -d --wait mongodb redis
    if($LASTEXITCODE -ne 0){throw 'Backend startup failed'}
    docker @composeArgs up -d --no-deps --force-recreate --wait api
    if($LASTEXITCODE -ne 0){throw 'API startup failed; inspect docker compose logs'}
    $base="http://127.0.0.1:$Port"
    $pointer=Get-Content -LiteralPath outputs/latest_run.json -Raw | ConvertFrom-Json
    $ready=Invoke-RestMethod -Uri "$base/health/ready" -TimeoutSec 30
    $metrics=Invoke-RestMethod -Uri "$base/api/metrics" -TimeoutSec 30
    if($metrics.run_id -ne $pointer.run_id -or $metrics.model_version -ne $pointer.model_version){throw 'Unexpected active model'}
    if(!$metrics.session_model.available){throw 'Session index is unavailable'}
    $body=@{events=@(@{itemid=129960;event='view'});k=10} | ConvertTo-Json -Depth 5
    $rec=Invoke-RestMethod -Uri "$base/api/session/recommendations" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 30
    if($rec.run_id -ne $pointer.run_id -or @($rec.items).Count -ne 10){throw 'Session smoke check failed'}
    if(@($rec.items.itemid) -contains 129960){throw 'Seen ID was recommended'}
    $evidence=[PSCustomObject]@{status='passed';url=$base;run_id=$metrics.run_id;model_version=$metrics.model_version;ready=$ready;sample=$rec;session_metrics=$metrics.session_model.metrics}
    New-Item -ItemType Directory -Path outputs/evidence/local_demo -Force | Out-Null
    $evidence | ConvertTo-Json -Depth 24 | Set-Content -LiteralPath "outputs/evidence/local_demo/startup-$Port.json" -Encoding UTF8
    Write-Host "UPDATE2 READY: http://localhost:$Port/#session-section | $($metrics.run_id)"
} finally {
    $env:APP_PORT=$savedPort
    Pop-Location
}
