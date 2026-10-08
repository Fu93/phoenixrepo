"""One-command foundation compliance check. No resurrection path."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from api.run import app
from evidence.integrity import verify_integrity
from evidence.pack import EvidencePack

client = TestClient(app)
CHECKS = []


def check(name: str, ok: bool) -> None:
    CHECKS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")


def main() -> int:
    print("PhoenixRepo Compliance Check")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8").lower()
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    check("Docker / CPU-only", "cuda" not in dockerfile and 'host="0.0.0.0"' in main_py)

    health = client.get("/health")
    check("API contract", health.status_code == 200 and health.json().get("service") == "phoenixrepo")

    response = client.post(
        "/run",
        json={"repository": "sample://foundation", "mode": "sample", "max_repair_iterations": 3},
    )
    data = response.json() if response.status_code == 200 else {}
    check("SAMPLE_MODE", data.get("foundation_only") is True and data.get("decision") is None)
    log = Path(data.get("log_file", ""))
    events = []
    if log.exists():
        events = [json.loads(line)["event"] for line in log.read_text(encoding="utf-8").splitlines()]
    check("Trace logging", "RUN_STARTED" in events and "FOUNDATION_COMPLETED" in events)

    pack_path = ROOT / "run_artifacts" / data.get("run_id", "") / "evidence-pack.json"
    pack_ok = False
    if pack_path.exists():
        pack_ok = verify_integrity(EvidencePack.model_validate(json.loads(pack_path.read_text())))
    check("Evidence artifacts", pack_ok and data.get("evidence_count", 0) > 0)
    check("Audit trail", data.get("audit_events", 0) >= 3)
    check("Evidence Graph", data.get("claims_count", 0) == 1 and data.get("state") == "EVIDENCE_COMPLETE")

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    check("Secret configuration", "sk-" not in env_example and "AIza" not in env_example)

    invalid = client.post("/run", json={"repository": "sample://foundation", "mode": "invalid"})
    live = client.post("/run", json={"repository": "sample://foundation", "mode": "live"})
    check("Validation boundaries", invalid.status_code == 422 and live.status_code == 400)

    failed = [name for name, ok in CHECKS if not ok]
    print("RESULT: PASS" if not failed else "RESULT: FAIL")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
