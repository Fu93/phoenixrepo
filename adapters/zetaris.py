"""Interface only. No scored evidence ingestion before 22 October 2026 00:00 UTC."""


class ZetarisAdapter:
    def __init__(self, mcp_url: str | None, api_key: str | None) -> None:
        self.mcp_url = mcp_url
        self.api_key = api_key

    def configured(self) -> bool:
        return bool(self.mcp_url and self.api_key)

    def query(self, statement: str) -> list[dict]:
        raise NotImplementedError("Zetaris query is wired after the build window opens.")
