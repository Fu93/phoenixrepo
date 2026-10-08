from security.sanitizer import sanitize_metadata


def test_secret_fields_are_redacted():
    cleaned = sanitize_metadata({"api_key": "real-value", "note": "ok", "nested": {"token": "abc"}})
    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["nested"]["token"] == "[REDACTED]"
    assert cleaned["note"] == "ok"
