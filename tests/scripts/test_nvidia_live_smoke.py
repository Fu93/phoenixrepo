from __future__ import annotations

from scripts import nvidia_live_smoke as smoke


def test_fixed_claim_is_provenance_only():
    assert "not an evaluation result" in smoke.FIXED_CLAIM_TEMPLATE
    assert "model-quality comparison" in smoke.FIXED_CLAIM_TEMPLATE
    assert "Gate 2 repair claim" in smoke.FIXED_CLAIM_TEMPLATE


def test_smoke_uses_the_ultra_request_by_default():
    assert smoke.DEFAULT_MODEL == "nvidia/nemotron-3-ultra-550b-a55b"

def test_claim_template_has_no_backticks_and_script_is_ascii():
    assert "`" not in smoke.FIXED_CLAIM_TEMPLATE
    assert "requested {requested} and was served by {served}." in smoke.FIXED_CLAIM_TEMPLATE
    source = open(smoke.__file__, encoding="utf-8").read()
    assert source.isascii()
    assert "super-vl" not in (smoke.__doc__ or "")