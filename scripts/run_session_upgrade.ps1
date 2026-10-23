param(
    [string]$SourceRun = '',
    [string]$ReferenceRun = '',
    [string]$RunId = ('session-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
)

$ErrorActionPreference = 'Stop'

function Assert-RunId([string]$Value, [string]$Name) {
    if ($Value -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$') {
        throw "$Name must contain only letters, digits, underscores or hyphens."
    }
}

function Read-RunReport([string]$Id) {
    Assert-RunId $Id 'Run ID'
    $path = Join-Path 'outputs/runs' "$Id/metrics/run_report.json"
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing report: $path. Restore the runtime output or train the prerequisite first."
    }
    return (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json)
}

function Invoke-LoggedDocker([string[]]$DockerArguments, [string]$LogPath) {
    # Windows PowerShell can classify native stderr as an error record. Keep the
    # native exit code authoritative while retaining both streams in evidence.
    $savedPreference = $ErrorActionPreference
    $nativeExit = 1
    try {
        $ErrorActionPreference = 'Continue'
        & docker @DockerArguments 2>&1 | Tee-Object -FilePath $LogPath
        $nativeExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($nativeExit -ne 0) {
        throw "Docker command failed with exit code $nativeExit. Inspect $LogPath."
    }
}

Push-Location (Join-Path $PSScriptRoot '..')
try {
    Assert-RunId $RunId 'RunId'
    if (Test-Path -LiteralPath "outputs/runs/$RunId") {
        throw "Run $RunId already exists. Choose a new -RunId; completed evidence is preserved."
    }

    if (!$ReferenceRun) {
        if (!(Test-Path -LiteralPath 'outputs/latest_run.json' -PathType Leaf)) {
            throw 'No active model. Run scripts/run_full_training.ps1 and scripts/run_neighborhood_upgrade.ps1 first.'
        }
        $cursor = (Get-Content -LiteralPath 'outputs/latest_run.json' -Raw | ConvertFrom-Json).run_id
        $visited = @{}
        for ($depth = 0; $depth -lt 64; $depth++) {
            Assert-RunId $cursor 'Reference lineage ID'
            if ($visited.ContainsKey($cursor)) { throw 'Reference lineage contains a cycle.' }
            $visited[$cursor] = $true
            $report = Read-RunReport $cursor
            if ($report.reference_run_id) {
                $cursor = [string]$report.reference_run_id
                continue
            }
            $bundlePath = "outputs/runs/$cursor/serving/model_bundle.json"
            if (Test-Path -LiteralPath $bundlePath -PathType Leaf) {
                $candidateBundle = Get-Content -LiteralPath $bundlePath -Raw | ConvertFrom-Json
                if ($candidateBundle.session_model.reference_run_id) {
                    $cursor = [string]$candidateBundle.session_model.reference_run_id
                    continue
                }
                if ($candidateBundle.algorithm -eq 'item_knn_hybrid' -and !$candidateBundle.session_model) {
                    $ReferenceRun = $cursor
                    break
                }
            }
            $cursor = [string]$report.source_run_id
            if (!$cursor) { break }
        }
        if (!$ReferenceRun) {
            throw 'No previous item-item reference found. Run scripts/run_neighborhood_upgrade.ps1 first, or specify -ReferenceRun.'
        }
    }

    Assert-RunId $ReferenceRun 'ReferenceRun'
    $referenceReport = Read-RunReport $ReferenceRun
    if ($referenceReport.status -ne 'completed') { throw 'Reference run must have status completed.' }
    $referenceBundlePath = "outputs/runs/$ReferenceRun/serving/model_bundle.json"
    if (!(Test-Path -LiteralPath $referenceBundlePath -PathType Leaf)) { throw 'Reference serving bundle is missing.' }
    $referenceBundle = Get-Content -LiteralPath $referenceBundlePath -Raw | ConvertFrom-Json
    if ($referenceBundle.algorithm -ne 'item_knn_hybrid') { throw 'Reference must be the previous item_knn_hybrid model.' }

    if (!$SourceRun) {
        $cursor = $ReferenceRun
        $visited = @{}
        for ($depth = 0; $depth -lt 64; $depth++) {
            Assert-RunId $cursor 'Source lineage ID'
            if ($visited.ContainsKey($cursor)) { throw 'Source lineage contains a cycle.' }
            $visited[$cursor] = $true
            if (Test-Path -LiteralPath "outputs/runs/$cursor/models/evaluation_pipeline" -PathType Container) {
                $SourceRun = $cursor
                break
            }
            $report = Read-RunReport $cursor
            $cursor = [string]$report.source_run_id
            if (!$cursor) { break }
        }
        if (!$SourceRun) { throw 'Full source lineage is missing. Run scripts/run_full_training.ps1 first.' }
    }

    Assert-RunId $SourceRun 'SourceRun'
    $sourceReport = Read-RunReport $SourceRun
    if ($sourceReport.status -ne 'completed') { throw 'Full source run must have status completed.' }
    if (!(Test-Path -LiteralPath "outputs/runs/$SourceRun/metrics/temporal_split.json" -PathType Leaf)) {
        throw 'Full source temporal split evidence is missing.'
    }
    if (!(Test-Path -LiteralPath "outputs/runs/$SourceRun/models/evaluation_pipeline" -PathType Container)) {
        throw 'Full source evaluation model is missing.'
    }

    & docker version --format '{{.Server.Version}}'
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop with the Linux engine before running this script.' }

    New-Item -ItemType Directory -Path 'outputs/evidence' -Force | Out-Null
    Write-Host "Source: $SourceRun | Reference: $ReferenceRun | New run: $RunId"
    Invoke-LoggedDocker -DockerArguments @('build', '-t', 'retailrocket-phase17:1.0', '.') -LogPath "outputs/evidence/$RunId-build.log"
    Invoke-LoggedDocker -DockerArguments @('compose', 'up', '-d', '--wait', 'namenode', 'datanode', 'mongodb', 'redis') -LogPath "outputs/evidence/$RunId-infrastructure.log"

    $sparkArguments = 'PYSPARK_SUBMIT_ARGS=--driver-memory 3g --conf spark.driver.maxResultSize=512m --conf spark.local.dir=/tmp/session_upgrade_spark --conf spark.sql.shuffle.partitions=24 pyspark-shell'
    Invoke-LoggedDocker -DockerArguments @(
        'compose', 'run', '--rm', '--no-deps',
        '-e', $sparkArguments,
        'app', 'python', '-m', 'pipeline.session_upgrade',
        '--source-run', $SourceRun, '--reference-run', $ReferenceRun, '--run-id', $RunId
    ) -LogPath "outputs/evidence/$RunId-training.log"

    Invoke-LoggedDocker -DockerArguments @(
        'compose', 'run', '--rm', '--no-deps', 'app',
        'python', 'scripts/verify_session_upgrade.py', '--run-id', $RunId
    ) -LogPath "outputs/evidence/$RunId-audit.log"

    $completedReport = Read-RunReport $RunId
    if ($completedReport.status -eq 'completed_not_promoted') {
        Write-Host "Experiment audited without promotion. The previous demo remains active. See outputs/runs/$RunId/metrics."
        return
    }
    if ($completedReport.status -ne 'completed') {
        throw "Unexpected final status: $($completedReport.status). Inspect outputs/runs/$RunId/metrics/run_report.json."
    }

    Invoke-LoggedDocker -DockerArguments @(
        'compose', 'up', '-d', '--no-deps', '--force-recreate', '--wait', 'api'
    ) -LogPath "outputs/evidence/$RunId-api.log"

    $ready = Invoke-RestMethod -Uri 'http://localhost:8000/health/ready' -TimeoutSec 30
    $metrics = Invoke-RestMethod -Uri 'http://localhost:8000/api/metrics' -TimeoutSec 30
    if ($metrics.run_id -ne $RunId) {
        throw "The API is serving $($metrics.run_id), expected $RunId. Inspect latest_run.json and API logs."
    }
    $staticResponse = Invoke-RestMethod -Uri 'http://localhost:8000/api/recommendations/2147483647?k=10' -TimeoutSec 30
    if (@($staticResponse.items).Count -lt 1) { throw 'Static recommendation smoke check returned no items.' }
    $seedItem = [int]$staticResponse.items[0].itemid
    $requestBody = @{ events = @(@{itemid = $seedItem; event = 'view'}); k = 10 } | ConvertTo-Json -Depth 6
    $sessionResponse = Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/api/session/recommendations' -ContentType 'application/json' -Body $requestBody -TimeoutSec 30
    if ($sessionResponse.run_id -ne $RunId) { throw 'Session response model version is not the newly activated run.' }
    if (@($sessionResponse.items).Count -lt 1) { throw 'Session smoke check returned no items.' }
    if (@($sessionResponse.items | Where-Object { $_.itemid -eq $seedItem }).Count -gt 0) {
        throw 'Session seen-item filter failed.'
    }
    @{
        run_id = $RunId
        checked_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        readiness = $ready
        static_response = $staticResponse
        session_request = ($requestBody | ConvertFrom-Json)
        session_response = $sessionResponse
        scope = 'Local smoke checks; these simulated events do not change offline evaluation metrics.'
    } | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath "outputs/evidence/$RunId-api-smoke.json" -Encoding UTF8

    Write-Host "Session demo ready: http://localhost:8000/ | Run $RunId"
    Write-Host "Metrics and ranking audit: outputs/runs/$RunId/metrics"
    Write-Host "Build/training/API logs: outputs/evidence/$RunId-*"
} finally {
    Pop-Location
}
