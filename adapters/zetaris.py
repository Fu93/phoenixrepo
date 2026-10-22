"""Pre-existing import path. The REST client lives in app.adapters.zetaris."""

from app.adapters.zetaris import ZetarisClient, ZetarisResult, assert_single_statement, rows_from_payload

__all__ = ["ZetarisClient", "ZetarisResult", "assert_single_statement", "rows_from_payload"]
