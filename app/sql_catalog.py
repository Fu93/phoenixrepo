"""SQL the agents are allowed to send. One statement, one view, one predicate."""

import re

REPO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_./:-]{1,200}$")

PROFILE_COLUMNS = (
    "repo_id",
    "full_name",
    "description",
    "licence_spdx",
    "primary_language",
    "stars",
    "forks",
    "archived",
    "days_since_push",
    "open_issues",
    "has_readme",
    "has_license_file",
    "has_tests",
    "has_ci",
    "ci_status",
    "similar_repo_count",
    "unique_capability",
    "successor_url",
    "category",
    "html_url",
    "data_origin",
)

SIGNAL_COLUMNS = ("repo_id", "signal", "detail", "supports_revival")

PERMISSIVE_LICENCES = frozenset(
    {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC", "MPL-2.0"}
)
ABANDONED_AFTER_DAYS = 180
SIMILAR_REPO_BLOCK = 40


def require_repo_id(repository: str) -> str:
    repo_id = repository.strip()
    if not REPO_ID_PATTERN.fullmatch(repo_id):
        raise ValueError(
            "repository must be 1-200 characters from letters, numbers, and _./:-"
        )
    return repo_id


def _quote(repo_id: str) -> str:
    return repo_id.replace("'", "''")


def profile_sql(repo_id: str) -> str:
    columns = ", ".join(PROFILE_COLUMNS)
    return (
        f"SELECT {columns} FROM phoenix.repo_evidence "
        f"WHERE repo_id = '{_quote(require_repo_id(repo_id))}'"
    )


def signals_sql(repo_id: str) -> str:
    columns = ", ".join(SIGNAL_COLUMNS)
    return (
        f"SELECT {columns} FROM phoenix.revival_signals "
        f"WHERE repo_id = '{_quote(require_repo_id(repo_id))}'"
    )
