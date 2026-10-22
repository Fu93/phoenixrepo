class InvestigationError(Exception):
    """Structured failure. The message must not contain credentials."""

    def __init__(self, error_type: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.status_code = status_code

    def body(self) -> dict:
        return {"error": {"type": self.error_type, "message": self.message}}


class ZetarisError(InvestigationError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__("ZetarisError", message, status_code)
