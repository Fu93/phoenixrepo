"""load_real.ps1 counts already-exists only on an error response.

The mock speaks HTTP. pwsh 7 reads the 400 body from ErrorDetails.Message.
A second check drives the Windows PowerShell 5.1 GetResponseStream branch
with a fake response object, because this environment has no Windows PowerShell.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "zetaris" / "load_real.ps1"
PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")


def _table(path: Path, columns: list[str]) -> dict:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {"headers": columns, "data": [[row[name] for name in columns] for row in rows]}


EVIDENCE = _table(ROOT / "data" / "real" / "repo_evidence.csv", ["repo_id", "data_origin"])
ATTEMPTS = _table(ROOT / "data" / "real" / "revival_attempts.csv", ["repo_id", "stage", "data_origin"])


class _Server(ThreadingHTTPServer):
    def __init__(self, mode: str):
        self.mode = mode
        self.bodies: list[bytes] = []
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        self.server.bodies.append(raw)  # type: ignore[attr-defined]
        sql = ""
        try:
            sql = str(json.loads(raw.decode("utf-8")).get("sql") or "")
        except json.JSONDecodeError:
            sql = ""
        code, payload = _reply(self.server.mode, sql)  # type: ignore[attr-defined]
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return


def _reply(mode: str, sql: str) -> tuple[int, dict]:
    if sql.startswith("SELECT repo_id, data_origin FROM phoenix.repo_evidence"):
        return 200, EVIDENCE
    if sql.startswith("SELECT repo_id, stage, data_origin FROM phoenix.revival_attempts"):
        return 200, ATTEMPTS
    if mode == "fresh":
        return 200, {"headers": ["note"], "data": [["already exists"]]}
    if mode == "exists":
        return 400, {"msg": 'A database named "PHOENIX_REAL" already exists.'}
    return 400, {"msg": "syntax error near SELECT"}


def _run(mode: str) -> subprocess.CompletedProcess[str]:
    server = _Server(mode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "ZETARIS_API_KEY": "local-test-key",
            "ZETARIS_REST_URL": f"http://127.0.0.1:{port}",
            "ZETARIS_ORG_ID": "1",
            "HOME": "/tmp",
        }
        return subprocess.run(
            [PWSH, "-NoProfile", "-File", str(SCRIPT)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()


def test_fresh_200_is_not_an_error_even_when_a_cell_says_already_exists():
    result = _run("fresh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "200 error-body" not in result.stdout
    assert "verified rows=" in result.stdout


def test_http_400_already_exists_is_ok():
    result = _run("exists")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "exists" in result.stdout
    assert "verified rows=" in result.stdout
    assert "200 error-body" not in result.stdout


def test_other_http_400_fails():
    result = _run("other")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "400" in result.stdout
    assert "verified rows=" not in result.stdout


def test_error_body_readers_cover_pwsh7_and_the_5_1_stream():
    command = r"""
. ./scripts/zetaris/load_real.ps1
$fromDetails = Read-HttpErrorBody ([pscustomobject]@{
  ErrorDetails = [pscustomobject]@{ Message = '{"msg":"A database named \"PHOENIX_REAL\" already exists."}' }
  Exception = $null
})
if (-not (Test-AlreadyExists $fromDetails)) { throw "ErrorDetails.Message was not read" }
$bytes = [Text.Encoding]::UTF8.GetBytes('{"msg":"syntax error near SELECT"}')
$response = New-Object PSObject
$response | Add-Member -MemberType ScriptMethod -Name GetResponseStream -Value {
  return New-Object System.IO.MemoryStream(,$bytes)
}.GetNewClosure()
$fromStream = Read-HttpErrorBody ([pscustomobject]@{
  ErrorDetails = $null
  Exception = [pscustomobject]@{ Response = $response }
})
if ($fromStream -notmatch 'syntax error') { throw "GetResponseStream was not read: $fromStream" }
$success = '{"headers":["note"],"data":[["already exists"]]}'
if (-not (Test-SuccessResult $success)) { throw "a 200 result set was treated as an error" }
$errorObject = '{"msg":"syntax error near SELECT"}'
if (Test-SuccessResult $errorObject) { throw "an error object was treated as a result set" }
Write-Output "readers-ok"
"""
    result = subprocess.run(
        [PWSH, "-NoProfile", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "readers-ok" in result.stdout
