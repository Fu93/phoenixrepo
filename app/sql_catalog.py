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

# One row per stage of a fork revival attempt. commit_sha stays empty until an
# attempt commit exists. head_sha is the observed default-branch SHA and is not
# itself an attempt commit.
ATTEMPT_COLUMNS = (
    "repo_id",
    "fork_full_name",
    "upstream_full_name",
    "stage",
    "status",
    "ci_status",
    "ci_run_url",
    "ci_run_name",
    "ci_run_event",
    "commit_sha",
    "compare_url",
    "default_branch",
    "head_sha",
    "upstream_base_sha",
    "ci_head_repo",
    "workflow_path",
    "workflow_steps",
    "workflow_runs_build",
    "compare_shas",
    "data_origin",
)

# Cited GitHub documents (workflow file, package.json, issue, compare, Actions).
# The planner may quote excerpt and url. It may not add facts that are not here.
SOURCE_COLUMNS = (
    "repo_id",
    "source_id",
    "kind",
    "title",
    "url",
    "excerpt",
    "data_origin",
)

# Discovery candidates. Stars, forks, and issues are stored and are not a gate.
DISCOVERY_COLUMNS = (
    "repo_id",
    "html_url",
    "description",
    "readme_excerpt",
    "problem_excerpt",
    "user_excerpt",
    "scope_excerpt",
    "code_paths",
    "created_at",
    "pushed_at",
    "days_since_push",
    "stargazers_count",
    "forks_count",
    "open_issues",
    "licence_spdx",
    "core_terms",
    "cohort",
    "data_origin",
    "collected_at",
)

# One row per recorded alternative. required_terms must be verbatim phrases.
PRIOR_ART_COLUMNS = (
    "repo_id",
    "alternative_id",
    "url",
    "licence_spdx",
    "stars",
    "pushed_at",
    "days_since_push",
    "excerpt",
    "candidate_excerpt",
    "required_terms",
    "data_origin",
    "collected_at",
)

# External demand for the problem. The candidate repository's own counts are not here.
DEMAND_COLUMNS = (
    "repo_id",
    "signal_id",
    "kind",
    "url",
    "excerpt",
    "metric_name",
    "metric_value",
    "comments",
    "reactions",
    "stars",
    "pushed_at",
    "days_since_push",
    "query",
    "data_origin",
    "collected_at",
)

PERMISSIVE_LICENCES = frozenset(
    {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC", "MPL-2.0"}
)
# Free and open-source licences an alternative may carry. Empty and NOASSERTION do not qualify.
# This set is wider than PERMISSIVE_LICENCES because a recommended tool may be copyleft.
FREE_OSS_LICENCES = PERMISSIVE_LICENCES | frozenset(
    {"GPL-2.0", "GPL-3.0", "LGPL-2.1", "LGPL-3.0", "AGPL-3.0", "Unlicense", "0BSD"}
)
ABANDONED_AFTER_DAYS = 180
# Bands for the close-substitute score. They describe the search total. They are not a NO_GO cut.
ALTERNATIVES_LOW_AT_MOST = 5
ALTERNATIVES_MODERATE_AT_MOST = 30


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


def attempts_sql(repo_id: str) -> str:
    columns = ", ".join(ATTEMPT_COLUMNS)
    return (
        f"SELECT {columns} FROM phoenix.revival_attempts "
        f"WHERE repo_id = '{_quote(require_repo_id(repo_id))}'"
    )


def sources_sql(repo_id: str) -> str:
    columns = ", ".join(SOURCE_COLUMNS)
    return (
        f"SELECT {columns} FROM phoenix.revival_sources "
        f"WHERE repo_id = '{_quote(require_repo_id(repo_id))}'"
    )


def discovery_sql(repo_id: str) -> str:
    columns = ", ".join(DISCOVERY_COLUMNS)
    return (
        f"SELECT {columns} FROM phoenix.discovery_candidates "
        f"WHERE repo_id = '{_quote(require_repo_id(repo_id))}'"
    )


def prior_art_sql(repo_id: str) -> str:
    columns = ", ".join(PRIOR_ART_COLUMNS)
    return (
        f"SELECT {columns} FROM phoenix.prior_art "
        f"WHERE repo_id = '{_quote(require_repo_id(repo_id))}'"
    )


def demand_sql(repo_id: str) -> str:
    columns = ", ".join(DEMAND_COLUMNS)
    return (
        f"SELECT {columns} FROM phoenix.demand_signals "
        f"WHERE repo_id = '{_quote(require_repo_id(repo_id))}'"
    )
