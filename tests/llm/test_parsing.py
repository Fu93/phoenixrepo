import pytest

from app.errors import InvestigationError
from app.llm.parsing import parse_model_json, prompt_json_block


def test_leading_reasoning_is_ignored_for_the_final_object():
    text = (
        "The user asks: explain the row. We must reply with exactly the object.\n"
        '{"decision": "NO_GO"}\n'
        '{"decision": "RESURRECTED", "explanation": "rows agree"}'
    )
    assert parse_model_json(text)["decision"] == "RESURRECTED"


def test_think_block_is_stripped_before_the_answer():
    text = (
        "<think>draft {\"decision\": \"NO_GO\"} then reconsider</think>\n"
        "The user asks: revive. We must reply with exactly\n"
        '{"decision": "RESURRECTED", "explanation": "MIT and 640 days"}'
    )
    parsed = parse_model_json(text)
    assert parsed["decision"] == "RESURRECTED"
    assert "640" in parsed["explanation"]


def test_nested_object_does_not_replace_its_parent():
    text = 'preamble {"decision": "RESURRECTED", "extra": {"days": 226}}'
    assert parse_model_json(text)["decision"] == "RESURRECTED"
    assert parse_model_json(text)["extra"]["days"] == 226


def test_prompt_json_block_still_reads_the_evidence_object():
    user = 'ROLE: archaeologist\nEVIDENCE_JSON:\n{"row": {"repo_id": "sample/phoenix-ledger"}}\nReturn one JSON object.'
    assert prompt_json_block(user)["row"]["repo_id"] == "sample/phoenix-ledger"


def test_missing_object_is_an_error():
    with pytest.raises(InvestigationError):
        parse_model_json("The user asks: explain. We must reply with exactly the answer.")
