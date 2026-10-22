import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)
FORBIDDEN = ("sk-", "AIza", "Bearer ", "api_key=", "password=")


def test_sample_artifacts_do_not_contain_secrets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = client.post(
        "/run",
        json={"repository": "sample/phoenix-ledger", "mode": "sample", "max_repair_iterations": 0},
    ).json()
    files = [
        Path("run_artifacts") / data["run_id"] / "evidence-pack.json",
        Path("run_artifacts") / data["run_id"] / "run-context.json",
        Path(data["log_file"]),
    ]
    blob = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for token in FORBIDDEN:
        assert token not in blob
    metadata = json.loads(files[0].read_text())["metadata"]
    assert metadata["foundation_only"] is False
    assert metadata["decision"] == "RESURRECTED"
