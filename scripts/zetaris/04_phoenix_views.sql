-- Semantic layer the agents query. Run each statement separately.
-- Container name: phoenix. View names must stay repo_evidence and revival_signals.
-- The agent sends exactly:
--   SELECT <columns> FROM phoenix.repo_evidence WHERE repo_id = '<owner/name>'
--   SELECT repo_id, signal, detail, supports_revival FROM phoenix.revival_signals WHERE repo_id = '<owner/name>'
--   SELECT <columns> FROM phoenix.revival_attempts WHERE repo_id = '<owner/name>'
--   SELECT <columns> FROM phoenix.revival_sources WHERE repo_id = '<owner/name>'
-- Removing these views breaks the investigation. There is no GitHub fallback in the app.
-- revival_attempts and revival_sources are file-backed. An empty result is a real
-- "not yet attempted" / "no cited source" answer, not a missing view.

CREATE SCHEMASTORE CONTAINER phoenix;

-- Next statement. Join the GitHub object to the curated columns.
CREATE SCHEMASTORE VIEW repo_evidence WITH CONTAINER phoenix AS
SELECT
  s.repo_id,
  r.full_name,
  r.description,
  r.license.spdx_id AS licence_spdx,
  r.language AS primary_language,
  r.stargazers_count AS stars,
  r.forks_count AS forks,
  r.archived AS archived,
  s.days_since_push,
  r.open_issues_count AS open_issues,
  s.has_readme,
  s.has_license_file,
  s.has_tests,
  s.has_ci,
  s.ci_status,
  s.similar_repo_count,
  s.unique_capability,
  s.successor_url,
  s.category,
  r.html_url,
  s.data_origin
FROM github_rest.repo_facts r
JOIN phoenix_files.curated_signals s
  ON r.full_name = s.repo_id;

-- Next statement.
CREATE SCHEMASTORE VIEW revival_signals WITH CONTAINER phoenix AS
SELECT repo_id, signal, detail, supports_revival
FROM phoenix_files.revival_signals_file;

-- Next statement. Fork attempt rows. One row per stage.
CREATE SCHEMASTORE VIEW revival_attempts WITH CONTAINER phoenix AS
SELECT
  repo_id,
  fork_full_name,
  upstream_full_name,
  stage,
  status,
  ci_status,
  ci_run_url,
  ci_run_name,
  ci_run_event,
  commit_sha,
  compare_url,
  default_branch,
  head_sha,
  upstream_base_sha,
  ci_head_repo,
  workflow_path,
  workflow_steps,
  workflow_runs_build,
  compare_shas,
  data_origin
FROM phoenix_files.revival_attempts_file;

-- Next statement. Documents the planner is allowed to quote.
CREATE SCHEMASTORE VIEW revival_sources WITH CONTAINER phoenix AS
SELECT repo_id, source_id, kind, title, url, excerpt, data_origin
FROM phoenix_files.revival_sources_file;
