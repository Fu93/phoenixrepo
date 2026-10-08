import hashlib
import json


def run_fingerprint(repository: str, mode: str, max_repair_iterations: int, pipeline_version: str) -> str:
    payload = {
        "repository": repository,
        "mode": mode,
        "max_repair_iterations": max_repair_iterations,
        "pipeline_version": pipeline_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
