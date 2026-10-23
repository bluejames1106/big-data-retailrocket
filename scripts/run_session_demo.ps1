param(
    [switch]$SkipBuild,
    [switch]$OpenBrowser,
    [string]$BaseUrl = 'http://localhost:8000'
)

$ErrorActionPreference = 'Stop'

function Invoke-DemoDocker([string[]]$DockerArguments, [string]$LogPath) {
    $savedPreference = $ErrorActionPreference
    $nativeExit = 1
    try {
        $ErrorActionPreference = 'Continue'
        & docker @DockerArguments 2>&1 | Tee-Object -FilePath $LogPath
        $nativeExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($nativeExit -ne 0) { throw "Docker failed with exit code $nativeExit. Inspect $LogPath." }
}

Push-Location (Join-Path $PSScriptRoot '..')
try {
    $BaseUrl = $BaseUrl.TrimEnd('/')
    if (!(Test-Path -LiteralPath 'outputs/latest_run.json' -PathType Leaf)) {
        throw 'This folder has no packaged serving snapshot. Use the complete Session ZIP, or run full/neighborhood/session training first.'
    }
    $pointer = Get-Content -LiteralPath 'outputs/latest_run.json' -Raw | ConvertFrom-Json
    $runId = [string]$pointer.run_id
    if ($runId -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$') { throw 'Invalid active run ID.' }
    $serving = "outputs/runs/$runId/serving"
    foreach ($name in @('model_bundle.json', 'seen_items.sqlite', 'session_neighbors.sqlite')) {
        $requiredPath = Join-Path $serving $name
        if (!(Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "The packaged serving snapshot is incomplete: $requiredPath. Restore the full Session ZIP or retrain."
        }
        if ((Get-Item -LiteralPath $requiredPath).Length -le 0) { throw "Empty serving artifact: $requiredPath" }
    }
    $bundle = Get-Content -LiteralPath (Join-Path $serving 'model_bundle.json') -Raw | ConvertFrom-Json
    if ($bundle.run_id -ne $runId -or $bundle.model_version -ne $pointer.model_version) {
        throw 'Active pointer and packaged model bundle disagree. Restore matching snapshot files.'
    }
    if ($bundle.session_model.index_file -ne 'session_neighbors.sqlite') {
        throw 'The active snapshot does not declare a usable session neighbor index.'
    }
    $dockerIgnore = Get-Content -LiteralPath '.dockerignore' -Raw
    if ($dockerIgnore -notmatch '(?m)^\s*/?outputs/?\s*$') {
        throw 'Keep outputs excluded in .dockerignore: serving artifacts are mounted read-only, not copied into the image.'
    }

    & docker version --format '{{.Server.Version}}'
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop with the Linux engine first.' }
    New-Item -ItemType Directory -Path 'outputs/evidence' -Force | Out-Null
    $evidencePrefix = "outputs/evidence/demo-$runId-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
    Write-Host "Portable serving snapshot: $runId"
    if (!$SkipBuild) {
        Invoke-DemoDocker -DockerArguments @('build', '-t', 'retailrocket-phase17:1.0', '.') -LogPath "$evidencePrefix-build.log"
    }
    Invoke-DemoDocker -DockerArguments @('compose', 'up', '-d', '--wait', 'mongodb', 'redis') -LogPath "$evidencePrefix-backends.log"
    # --no-deps is deliberate: this serving-only demo does not require HDFS,
    # Kafka, Spark training, or Airflow. Existing unrelated containers stay as-is.
    Invoke-DemoDocker -DockerArguments @('compose', 'up', '-d', '--no-deps', '--force-recreate', '--wait', 'api') -LogPath "$evidencePrefix-api.log"

    $ready = Invoke-RestMethod -Uri "$BaseUrl/health/ready" -TimeoutSec 30
    $metrics = Invoke-RestMethod -Uri "$BaseUrl/api/metrics" -TimeoutSec 30
    if ($metrics.run_id -ne $runId -or $metrics.model_version -ne $bundle.model_version) {
        throw 'The API is serving a different model from the packaged snapshot.'
    }
    if (!$metrics.session_model.available) { throw 'The API cannot read the packaged session graph. Inspect /api/metrics.' }
    $staticResponse = Invoke-RestMethod -Uri "$BaseUrl/api/recommendations/2147483647?k=10" -TimeoutSec 30
    if (@($staticResponse.items).Count -lt 1) { throw 'Static recommendation smoke check returned no items.' }
    $seed = [int]$staticResponse.items[0].itemid
    $context = @(@{itemid = $seed; event = 'view'})
    $body = @{events = $context; k = 10} | ConvertTo-Json -Depth 6
    $first = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/session/recommendations" -ContentType 'application/json' -Body $body -TimeoutSec 30
    if ($first.run_id -ne $runId -or @($first.items).Count -lt 1 -or $first.context_events -ne 1) {
        throw 'First session smoke check failed.'
    }
    if (@($first.items | Where-Object { $_.itemid -eq $seed }).Count -gt 0) { throw 'The viewed seed was recommended again.' }
    $cartItem = [int]$first.items[0].itemid
    $context += @{itemid = $cartItem; event = 'addtocart'}
    $body = @{events = $context; k = 10} | ConvertTo-Json -Depth 6
    $second = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/session/recommendations" -ContentType 'application/json' -Body $body -TimeoutSec 30
    if ($second.run_id -ne $runId -or @($second.items).Count -lt 1 -or $second.context_events -ne 2) {
        throw 'Second session smoke check failed.'
    }
    if (@($second.items | Where-Object { $_.itemid -eq $seed -or $_.itemid -eq $cartItem }).Count -gt 0) {
        throw 'Session history filtering failed after the cart action.'
    }
    @{
        run_id = $runId
        checked_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        scope = 'Packaged serving snapshot only. No HDFS import or model training was performed.'
        readiness = $ready
        static_response = $staticResponse
        first_context = @(@{itemid = $seed; event = 'view'})
        first_response = $first
        second_context = $context
        second_response = $second
    } | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath "$evidencePrefix-smoke.json" -Encoding UTF8

    Write-Host "Demo ready: $BaseUrl/ | Run $runId"
    Write-Host "Session sources: $($first.source) -> $($second.source)"
    Write-Host "Smoke evidence: $evidencePrefix-smoke.json"
    if ($OpenBrowser) { Start-Process "$BaseUrl/" }
} finally {
    Pop-Location
}
