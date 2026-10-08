from security.policy import FORBIDDEN_FIELDS


def sanitize_metadata(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key.lower() in {name.lower() for name in FORBIDDEN_FIELDS}:
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = sanitize_metadata(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize_metadata(item) for item in value]
    return value
