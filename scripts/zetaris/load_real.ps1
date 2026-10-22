# Load data/real into a local Zetaris and point phoenix.repo_evidence at it.
# UNVERIFIED against a running Zetaris. Prints a status token only, never the body or the key.
# A HTTP 200 whose JSON body contains an error object is a failure, unless the
# error text says the schemastore container, view, database, or table already
# exists. The match is the phrase "already exists" only. The word "duplicate" is
# not treated as existence. Re-running is then idempotent for those objects.
# If a schemastore view already exists, the script prints view-exists and a warning:
# that view is left in place, and if it came from 04_phoenix_views.sql it is still
# the GitHub-join view. Dropping it was not verified, so this script does not guess
# a DROP statement. After the statements, it selects repo_id and data_origin from
# phoenix.repo_evidence and checks the row count and that data_origin starts with
# github_rest_api. A mismatch prints verify-failed.
#
# Requires ZETARIS_API_KEY in the environment. It is sent as a bearer token and
# is never printed. Optional: ZETARIS_REST_URL (default http://localhost:8888/api/v1.0),
# ZETARIS_ORG_ID (default 1), and -ContainerPath (default /opt/phoenixrepo/data/real).
#
# -Alternative Filestore  (default) CREATE LIGHTNING FILESTORE TABLE ... FORMAT CSV
# -Alternative SparkCsv   CREATE TABLE ... USING com.databricks.spark.csv
#
# Neither statement form was executed from the Linux agent that added this file.
# The Lightning SQL manual documents both. The getting-started confirmation for
# FILESTORE is a public S3 path, not a laptop bind mount. If CREATE SCHEMASTORE
# VIEW fails, the existing phoenix.repo_evidence view from 04_phoenix_views.sql
# is still the GitHub-join view and does not show these CSVs. This script does
# not drop that view.

param(
    [ValidateSet("Filestore", "SparkCsv")]
    [string]$Alternative = "Filestore",
    [string]$ContainerPath = "/opt/phoenixrepo/data/real"
)

$ErrorActionPreference = "Stop"
$key = [Environment]::GetEnvironmentVariable("ZETARIS_API_KEY")
if ([string]::IsNullOrWhiteSpace($key)) {
    Write-Error "ZETARIS_API_KEY is missing."
    exit 2
}
$base = [Environment]::GetEnvironmentVariable("ZETARIS_REST_URL")
if ([string]::IsNullOrWhiteSpace($base)) {
    $base = "http://localhost:8888/api/v1.0"
}
$org = [Environment]::GetEnvironmentVariable("ZETARIS_ORG_ID")
if ([string]::IsNullOrWhiteSpace($org)) {
    $org = "1"
}
$base = $base.TrimEnd("/")
$evidence = ($ContainerPath.TrimEnd("/") + "/repo_evidence.csv").Replace("\", "/")
$signals = ($ContainerPath.TrimEnd("/") + "/revival_signals.csv").Replace("\", "/")

$profileColumns = @(
    "repo_id", "full_name", "description", "licence_spdx", "primary_language",
    "stars", "forks", "archived", "days_since_push", "open_issues", "has_readme",
    "has_license_file", "has_tests", "has_ci", "ci_status", "similar_repo_count",
    "unique_capability", "successor_url", "category", "html_url", "data_origin"
) -join ", "

function New-Statement([string]$Kind, [string]$Sql) {
    return [pscustomobject]@{ Kind = $Kind; Sql = $Sql }
}

if ($Alternative -eq "Filestore") {
    $statements = @(
        (New-Statement "database" 'CREATE LIGHTNING DATABASE PHOENIX_REAL DESCRIBE BY "PhoenixRepo committed GitHub snapshot"'),
        (New-Statement "table" @"
CREATE LIGHTNING FILESTORE TABLE repo_evidence_file FROM PHOENIX_REAL FORMAT CSV OPTIONS (
  path "$evidence",
  header "true",
  delimiter ","
)
"@),
        (New-Statement "table" @"
CREATE LIGHTNING FILESTORE TABLE revival_signals_file FROM PHOENIX_REAL FORMAT CSV OPTIONS (
  path "$signals",
  header "true",
  delimiter ","
)
"@),
        (New-Statement "container" "CREATE SCHEMASTORE CONTAINER phoenix"),
        (New-Statement "view" "CREATE SCHEMASTORE VIEW repo_evidence WITH CONTAINER phoenix AS SELECT $profileColumns FROM phoenix_real.repo_evidence_file"),
        (New-Statement "view" "CREATE SCHEMASTORE VIEW revival_signals WITH CONTAINER phoenix AS SELECT repo_id, signal, detail, supports_revival FROM phoenix_real.revival_signals_file")
    )
}
else {
    $statements = @(
        (New-Statement "table" @"
CREATE TABLE phoenix_real_repo_evidence USING com.databricks.spark.csv OPTIONS (
  path "$evidence",
  header "true",
  inferSchema "false"
)
"@),
        (New-Statement "table" @"
CREATE TABLE phoenix_real_revival_signals USING com.databricks.spark.csv OPTIONS (
  path "$signals",
  header "true",
  inferSchema "false"
)
"@),
        (New-Statement "container" "CREATE SCHEMASTORE CONTAINER phoenix"),
        (New-Statement "view" "CREATE SCHEMASTORE VIEW repo_evidence WITH CONTAINER phoenix AS SELECT $profileColumns FROM phoenix_real_repo_evidence"),
        (New-Statement "view" "CREATE SCHEMASTORE VIEW revival_signals WITH CONTAINER phoenix AS SELECT repo_id, signal, detail, supports_revival FROM phoenix_real_revival_signals")
    )
}

function Get-ZetarisErrorText([string]$content) {
    if ([string]::IsNullOrWhiteSpace($content)) { return "" }
    try { $parsed = $content | ConvertFrom-Json } catch { return "" }
    if ($null -eq $parsed.error) { return "" }
    if ($parsed.error.message) { return [string]$parsed.error.message }
    return [string]$parsed.error
}

function Test-AlreadyExists([string]$text) {
    return $text -match "(?i)already exists|already exist"
}

function Invoke-ZetarisSql([string]$sql) {
    $body = @{
        queryId = [guid]::NewGuid().ToString()
        sql     = $sql
        source  = "SqlEditor"
        limit   = 100
    } | ConvertTo-Json -Compress
    $headers = @{
        Authorization  = "Bearer $key"
        "X-Org-ID"     = $org
        "X-Request-ID" = [guid]::NewGuid().ToString()
    }
    $code = 0
    $content = ""
    try {
        $response = Invoke-WebRequest -Uri "$base/sql-editor/sqls/run" -Method Post -Headers $headers -Body $body -ContentType "application/json; charset=utf-8" -UseBasicParsing
        $code = [int]$response.StatusCode
        $content = [string]$response.Content
    }
    catch {
        $resp = $_.Exception.Response
        if ($null -eq $resp) {
            return @{ Code = 0; Content = ""; Unreachable = $true }
        }
        $code = [int]$resp.StatusCode
        try {
            $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
            $content = $reader.ReadToEnd()
        }
        catch { $content = "" }
    }
    return @{ Code = $code; Content = $content; Unreachable = $false }
}

$failed = $false
foreach ($statement in $statements) {
    $flat = (($statement.Sql -replace "\s+", " ").Trim())
    $result = Invoke-ZetarisSql $flat
    if ($result.Unreachable) {
        Write-Output "unreachable"
        exit 1
    }
    $code = [int]$result.Code
    $content = [string]$result.Content
    $errorText = Get-ZetarisErrorText $content
    $exists = ($statement.Kind -in @("database", "table", "container", "view")) -and (Test-AlreadyExists $errorText)
    if ($exists) {
        if ($statement.Kind -eq "view") {
            Write-Warning "A schemastore view already exists and was left in place. If it came from scripts/zetaris/04_phoenix_views.sql, phoenix.repo_evidence is still the GitHub-join view and does not read data/real. This script does not drop it."
            Write-Output "view-exists"
        }
        else {
            Write-Output "exists"
        }
        continue
    }
    if ($errorText -or $code -ge 300 -or $code -eq 0) {
        if ($errorText -and $code -ge 200 -and $code -lt 300) { Write-Output "200 error-body" }
        elseif ($code -eq 0) { Write-Output "unreachable" }
        else { Write-Output $code }
        $failed = $true
        continue
    }
    Write-Output $code
}
if ($failed) { exit 1 }

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$localCsv = Join-Path $root "data/real/repo_evidence.csv"
$expected = 0
if (Test-Path -LiteralPath $localCsv) {
    $expected = @(Import-Csv -LiteralPath $localCsv).Count
}
$check = Invoke-ZetarisSql "SELECT repo_id, data_origin FROM phoenix.repo_evidence"
if ($check.Unreachable) {
    Write-Output "verify-failed"
    exit 1
}
$checkError = Get-ZetarisErrorText ([string]$check.Content)
$verified = $false
if (-not $checkError -and [int]$check.Code -ge 200 -and [int]$check.Code -lt 300) {
    try {
        $parsed = [string]$check.Content | ConvertFrom-Json
        $headers = @($parsed.headers | ForEach-Object { "$_".ToLowerInvariant() })
        $originIndex = [array]::IndexOf($headers, "data_origin")
        $rows = @($parsed.data)
        $originsOk = $originIndex -ge 0 -and $rows.Count -eq $expected -and $expected -gt 0
        if ($originsOk) {
            foreach ($line in $rows) {
                $cells = @($line)
                if ($originIndex -ge $cells.Count -or -not ([string]$cells[$originIndex]).StartsWith("github_rest_api")) {
                    $originsOk = $false
                    break
                }
            }
        }
        $verified = $originsOk
    }
    catch { $verified = $false }
}
if ($verified) {
    Write-Output "verified rows=$expected"
    exit 0
}
Write-Output "verify-failed"
exit 1
