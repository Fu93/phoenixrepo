import json
import re

from app.errors import InvestigationError

_THINK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def _last_json_object(text: str) -> dict | None:
    """Return the JSON object that ends last. Nested objects lose to their parent."""

    decoder = json.JSONDecoder()
    found: dict | None = None
    found_end = -1
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, offset = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        end = index + offset
        if isinstance(payload, dict) and end > found_end:
            found = payload
            found_end = end
    return found


def parse_model_json(text: str) -> dict:
    """Accept a JSON object after reasoning prose, think tags, or a code fence.

    Nightshift reasoning models prepend text such as "The user asks: ... We must
    reply with exactly" and may wrap a draft in <think>. The answer is the last
    JSON object in what remains.
    """

    raw = _THINK.sub("", text).strip()
    payload = _last_json_object(raw)
    if payload is None:
        raise InvestigationError("ModelOutputInvalid", "The model did not return a JSON object.", status_code=502)
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
