import hashlib
import json
from typing import Any


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def verify_integrity(pack) -> bool:
    if not pack.integrity_hash:
        return False
    payload = pack.model_dump(mode="json", exclude={"integrity_hash"})
    return content_hash(payload) == pack.integrity_hash
