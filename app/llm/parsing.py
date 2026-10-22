import json
import re

from app.errors import InvestigationError

_THINK_CLOSED = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think>", re.IGNORECASE)
_DEFAULT_KEYS = ("decision", "claims", "steps", "contract", "explanation", "action", "reason", "claim_ids")


def _visible_answer(text: str) -> str:
    """Drop closed think blocks, then ignore everything after an unclosed one."""

    stripped = _THINK_CLOSED.sub("", text)
    match = _THINK_OPEN.search(stripped)
    if match:
        stripped = stripped[: match.start()]
    return stripped


def _json_objects(text: str) -> list[tuple[dict, int]]:
    """Every JSON object, with the index where it ends. A parent ends after its children."""

    decoder = json.JSONDecoder()
    found: list[tuple[dict, int]] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, offset = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            found.append((payload, index + offset))
    return found


def parse_model_json(text: str, expected_keys: tuple[str, ...] | None = None) -> dict:
    """Accept a JSON object after reasoning prose, think tags, or a code fence.

    Nightshift reasoning models prepend text and may wrap a draft in <think>.
    Text after an unclosed <think> is ignored. When several objects remain, the
    one that contains the expected keys wins. Ties keep the object that ends last,
    so a parent wins over a nested object and a revised answer wins over a draft.
    """

    keys = expected_keys if expected_keys is not None else _DEFAULT_KEYS
    found = _json_objects(_visible_answer(text))
    if not found:
        raise InvestigationError("ModelOutputInvalid", "The model did not return a JSON object.", status_code=502)

    def rank(payload: dict) -> int:
        return sum(1 for key in keys if key in payload)

    best = max(rank(payload) for payload, _end in found)
    pool = [(payload, end) for payload, end in found if rank(payload) == best]
    return max(pool, key=lambda item: item[1])[0]


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
