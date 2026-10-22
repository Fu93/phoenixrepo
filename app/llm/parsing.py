import json
import re

from app.errors import InvestigationError

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_model_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = _FENCE.sub("", raw).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise InvestigationError("ModelOutputInvalid", "The model did not return a JSON object.", status_code=502)
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise InvestigationError("ModelOutputInvalid", "The model returned invalid JSON.", status_code=502) from exc
    if not isinstance(payload, dict):
        raise InvestigationError("ModelOutputInvalid", "The model JSON was not an object.", status_code=502)
    return payload


def prompt_field(user: str, name: str) -> str:
    prefix = f"{name}:"
    for line in user.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def prompt_json_block(user: str) -> dict:
    marker = "EVIDENCE_JSON:"
    index = user.find(marker)
    if index < 0:
        raise InvestigationError("ModelOutputInvalid", "Prompt is missing EVIDENCE_JSON.", status_code=500)
    return parse_model_json(user[index + len(marker) :])
