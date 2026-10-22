-- Semantic layer the agents query. Run each statement separately.
-- Container name: phoenix. View names must stay repo_evidence and revival_signals.
-- The agent sends exactly:
--   SELECT <columns> FROM phoenix.repo_evidence WHERE repo_id = '<owner/name>'
--   SELECT repo_id, signal, detail, supports_revival FROM phoenix.revival_signals WHERE repo_id = '<owner/name>'
-- Removing these views breaks the investigation. There is no GitHub fallback in the app.

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
