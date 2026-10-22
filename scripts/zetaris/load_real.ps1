# Load data/real into a local Zetaris and point phoenix.repo_evidence at it.
# UNVERIFIED against a running driver. This script only prints HTTP status codes.
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

if ($Alternative -eq "Filestore") {
    $statements = @(
        'CREATE LIGHTNING DATABASE PHOENIX_REAL DESCRIBE BY "PhoenixRepo committed GitHub snapshot"',
        @"
CREATE LIGHTNING FILESTORE TABLE repo_evidence_file FROM PHOENIX_REAL FORMAT CSV OPTIONS (
  path "$evidence",
  header "true",
  delimiter ","
)
"@,
        @"
CREATE LIGHTNING FILESTORE TABLE revival_signals_file FROM PHOENIX_REAL FORMAT CSV OPTIONS (
  path "$signals",
  header "true",
  delimiter ","
)
"@,
        "CREATE SCHEMASTORE CONTAINER phoenix",
        "CREATE SCHEMASTORE VIEW repo_evidence WITH CONTAINER phoenix AS SELECT $profileColumns FROM phoenix_real.repo_evidence_file",
        "CREATE SCHEMASTORE VIEW revival_signals WITH CONTAINER phoenix AS SELECT repo_id, signal, detail, supports_revival FROM phoenix_real.revival_signals_file"
    )
}
else {
    $statements = @(
        @"
CREATE TABLE phoenix_real_repo_evidence USING com.databricks.spark.csv OPTIONS (
  path "$evidence",
  header "true",
  inferSchema "false"
)
"@,
        @"
CREATE TABLE phoenix_real_revival_signals USING com.databricks.spark.csv OPTIONS (
  path "$signals",
  header "true",
  inferSchema "false"
)
"@,
        "CREATE SCHEMASTORE CONTAINER phoenix",
        "CREATE SCHEMASTORE VIEW repo_evidence WITH CONTAINER phoenix AS SELECT $profileColumns FROM phoenix_real_repo_evidence",
        "CREATE SCHEMASTORE VIEW revival_signals WITH CONTAINER phoenix AS SELECT repo_id, signal, detail, supports_revival FROM phoenix_real_revival_signals"
    )
}

$failed = $false
foreach ($sql in $statements) {
    $flat = (($sql -replace "\s+", " ").Trim())
    $body = @{
        queryId = [guid]::NewGuid().ToString()
        sql     = $flat
        source  = "SqlEditor"
        limit   = 100
    } | ConvertTo-Json -Compress
    $headers = @{
        Authorization  = "Bearer $key"
        "X-Org-ID"     = $org
        "X-Request-ID" = [guid]::NewGuid().ToString()
    }
    try {
        $response = Invoke-WebRequest -Uri "$base/sql-editor/sqls/run" -Method Post -Headers $headers -Body $body -ContentType "application/json; charset=utf-8" -UseBasicParsing
        Write-Output ([int]$response.StatusCode)
        if ([int]$response.StatusCode -ge 300) { $failed = $true }
    }
    catch {
        $resp = $_.Exception.Response
        if ($null -eq $resp) {
            Write-Output "unreachable"
            exit 1
        }
        $code = [int]$resp.StatusCode
        Write-Output $code
        $failed = $true
    }
}
if ($failed) { exit 1 }
exit 0
