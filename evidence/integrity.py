import hashlib
import json
from typing import Any


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def pack_payload(pack) -> dict:
    return {
        "schema_version": pack.schema_version,
        "run_id": pack.run_id,
        "evidence": pack.evidence,
        "claims": pack.claims,
        "relations": pack.relations,
        "metadata": pack.metadata,
    }


def verify_integrity(pack) -> bool:
    if not pack.integrity_hash:
        return False
    return content_hash(pack_payload(pack)) == pack.integrity_hash
