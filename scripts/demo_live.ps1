# One-command live demo for a Windows machine that already has Docker, Zetaris,
# Nightshift, and this API running. This file was not executed from the Linux
# agent that added it. It prints status codes, not response bodies or keys.
#
# Start the API first, in live mode, with ZETARIS_API_KEY and
# METERLESS_RUNTIME_TOKEN set in that process:
#   py -3 run.py
# Then:
#   powershell -ExecutionPolicy Bypass -File scripts\demo_live.ps1
#
# Defaults probe Nightshift at http://127.0.0.1:8088/v1/models, Zetaris at
# http://localhost:8888/api/v1.0, and POST /run at http://127.0.0.1:8000/run.

$ErrorActionPreference = "Stop"
Set-ExecutionPolicy -Scope Process Bypass -Force
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null

function Add-Check([System.Collections.Generic.List[object]]$checks, [string]$name, [string]$result) {
    $checks.Add([ordered]@{ name = $name; result = $result })
    Write-Output "$name $result"
}

$checks = New-Object System.Collections.Generic.List[object]
$docker = & docker version --format "{{.Server.Version}}" 2>$null
if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($docker)) {
    Add-Check $checks "docker" "ok"
}
else {
    Add-Check $checks "docker" "unreachable"
}

$models = "unreachable"
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:8088/v1/models" -Method Get -UseBasicParsing
    $models = [string]([int]$response.StatusCode)
}
catch {
    if ($_.Exception.Response) { $models = [string]([int]$_.Exception.Response.StatusCode) }
}
Add-Check $checks "nightshift_models" $models

$zetaris = "unreachable"
$key = [Environment]::GetEnvironmentVariable("ZETARIS_API_KEY")
$rest = [Environment]::GetEnvironmentVariable("ZETARIS_REST_URL")
if ([string]::IsNullOrWhiteSpace($rest)) { $rest = "http://localhost:8888/api/v1.0" }
$org = [Environment]::GetEnvironmentVariable("ZETARIS_ORG_ID")
if ([string]::IsNullOrWhiteSpace($org)) { $org = "1" }
if ([string]::IsNullOrWhiteSpace($key)) {
    $zetaris = "missing_key"
}
else {
    $body = @{
        queryId = [guid]::NewGuid().ToString()
        sql     = "SELECT 1 AS ok"
        source  = "SqlEditor"
        limit   = 1
    } | ConvertTo-Json -Compress
    $headers = @{
        Authorization  = "Bearer $key"
        "X-Org-ID"     = $org
        "X-Request-ID" = [guid]::NewGuid().ToString()
    }
    try {
        $response = Invoke-WebRequest -Uri ($rest.TrimEnd("/") + "/sql-editor/sqls/run") -Method Post -Headers $headers -Body $body -ContentType "application/json; charset=utf-8" -UseBasicParsing
        $zetaris = [string]([int]$response.StatusCode)
    }
    catch {
        if ($_.Exception.Response) { $zetaris = [string]([int]$_.Exception.Response.StatusCode) }
    }
}
Add-Check $checks "zetaris_select_1" $zetaris

$runs = New-Object System.Collections.Generic.List[object]
$ready = ($models -eq "200" -and $zetaris -eq "200")
if (-not $ready) {
    Add-Check $checks "post_run" "skipped"
}
else {
    $repos = @("golang/dep", "jxnblk/rgx", "discreteoptimization/setcover")
    foreach ($repo in $repos) {
        $payload = @{ repository = $repo; mode = "live"; max_repair_iterations = 0 } | ConvertTo-Json -Compress
        $item = [ordered]@{ repository = $repo; http_status = "unreachable"; decision = ""; state = ""; reason = ""; log_file = "" }
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/run" -Method Post -Body $payload -ContentType "application/json; charset=utf-8" -UseBasicParsing
            $item.http_status = [string]([int]$response.StatusCode)
            $parsed = $response.Content | ConvertFrom-Json
            $item.decision = [string]$parsed.decision
            $item.state = [string]$parsed.state
            $item.reason = [string]$parsed.metadata.reason
            $item.log_file = [string]$parsed.log_file
        }
        catch {
            if ($_.Exception.Response) {
                $item.http_status = [string]([int]$_.Exception.Response.StatusCode)
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $text = $reader.ReadToEnd()
                $item.reason = $text
            }
        }
        $runs.Add($item)
        Write-Output "$repo $($item.http_status) $($item.decision)"
    }
}

$checkPath = Join-Path $root "logs\demo-live-checks.json"
$runPath = Join-Path $root "logs\demo-live-runs.json"
$summary = Join-Path $root "logs\demo-live.md"
($checks | ConvertTo-Json -Depth 4) | Set-Content -Path $checkPath -Encoding utf8
($runs | ConvertTo-Json -Depth 4) | Set-Content -Path $runPath -Encoding utf8
py -3 (Join-Path $root "scripts\demo_summary.py") --checks $checkPath --runs $runPath --output $summary
if (-not $ready) { exit 1 }
exit 0
