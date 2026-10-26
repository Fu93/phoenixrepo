# Discovery PRD: SergioMendolia/ytkb

Drafted by: deterministic. llm_drafted: false.
Run hash: not sealed; this file is the deterministic snapshot, not an investigation run
Window: Finish each stage before 27 Oct 2026 23:45 UTC.

## Problem statement

Non-factual framing. This section sets the task. It is not a cited evidence claim.

Plan a product pass for SergioMendolia/ytkb (https://github.com/SergioMendolia/ytkb). Stages 1 to 3 are a plan only: this run does not fork or commit. Stage 4 states the licence gap, the install path already in the README, and the feature scope. This problem statement is task framing, not a cited evidence claim.

## Evidence-backed findings

- F1: Problem cited from the description: A CLI application for syncing YouTrack knowledge base articles to local markdown files, edit them in your editor of choice and push them back online. Target user cited from the README: edit them in your editor of choice Scope cited from the README: Push changes back to YouTrack (update only, no creation)
  - evidence: EV-DISCOVERY-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, html_url, description, readme_excerpt, problem_excerpt, user_excerpt, scope_excerpt, code_paths, created_at, pushed_at, days_since_push, stargazers_count, forks_count, open_issues, licence_spdx, core_terms, cohort, data_origin, collected_at FROM phoenix.discovery_candidates WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: ac02bd72d438940eae4e6bbb243ec44a89849595a0b00ed5036d34250de367af
- F2: Code paths beyond a template: main.go, cmd/push.go, cmd/download.go, internal/api/client.go, internal/markdown/parser.go, internal/filesystem/files.go. Pushed 2026-03-31T19:56:26Z, 205 days before 2026-10-22T19:11:28+00:00. Stars 0 are recorded and are not the demand signal. licence_spdx is empty.
  - evidence: EV-DISCOVERY-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, html_url, description, readme_excerpt, problem_excerpt, user_excerpt, scope_excerpt, code_paths, created_at, pushed_at, days_since_push, stargazers_count, forks_count, open_issues, licence_spdx, core_terms, cohort, data_origin, collected_at FROM phoenix.discovery_candidates WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: ac02bd72d438940eae4e6bbb243ec44a89849595a0b00ed5036d34250de367af
- F3: Demand kind github_issue signal foliant-pr-80 url https://github.com/foliant-docs/foliant/pull/80 comments 2 reactions 2 Excerpt: Adds a new built-in preprocessor (`youtrack`) that fetches issues and knowledge base articles from a YouTrack instance via the REST API and generates markdown files for inclusion in Foliant projects..
  - evidence: EV-DEMAND-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, signal_id, kind, url, excerpt, metric_name, metric_value, comments, reactions, stars, pushed_at, days_since_push, query, data_origin, collected_at FROM phoenix.demand_signals WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: d60b606614a33e1f5df065bdf9392de2f350b69bc56fbdea76de3a6e59de40d1
- F4: Demand kind github_search signal ytkb-issue-search url https://api.github.com/search/issues?q=%22knowledge%20base%22%20youtrack%20markdown%20is%3Aissue query "knowledge base" youtrack markdown is:issue metric total_count=10.
  - evidence: EV-DEMAND-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, signal_id, kind, url, excerpt, metric_name, metric_value, comments, reactions, stars, pushed_at, days_since_push, query, data_origin, collected_at FROM phoenix.demand_signals WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: d60b606614a33e1f5df065bdf9392de2f350b69bc56fbdea76de3a6e59de40d1
- F5: Demand kind github_search signal ytkb-repo-search url https://api.github.com/search/repositories?q=youtrack%20markdown%20knowledge%20in%3Adescription%20stars%3A%3E20%20pushed%3A%3E2026-04-22 query youtrack markdown knowledge in:description stars:>20 pushed:>2026-04-22 metric total_count=0.
  - evidence: EV-DEMAND-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, signal_id, kind, url, excerpt, metric_name, metric_value, comments, reactions, stars, pushed_at, days_since_push, query, data_origin, collected_at FROM phoenix.demand_signals WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: d60b606614a33e1f5df065bdf9392de2f350b69bc56fbdea76de3a6e59de40d1
- F6: Prior art foliant-docs/foliant (https://github.com/foliant-docs/foliant) licence MIT, stars 177, pushed 2026-03-29T13:40:56Z, 207 days before collection. Required terms: knowledge base|push changes back. Excerpt: Comprehensive markdown-based documentation toolkit Foliant is an all-in-one single-source documentation authoring tool.
  - evidence: EV-PRIOR-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, alternative_id, url, licence_spdx, stars, pushed_at, days_since_push, excerpt, candidate_excerpt, required_terms, data_origin, collected_at FROM phoenix.prior_art WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: 11ccfcd37962909c0fd87e156291e7e9a035f7f89f5e3b2e3b64785ed4cd982f
- F7: Prior art billpeet/yt-cli (https://github.com/billpeet/yt-cli) licence empty, stars 0, pushed 2026-08-17T07:39:14Z, 66 days before collection. Required terms: knowledge base|push changes back. Excerpt: YouTrack CLI tool for AI agent and developer use — text/markdown output by default
  - evidence: EV-PRIOR-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, alternative_id, url, licence_spdx, stars, pushed_at, days_since_push, excerpt, candidate_excerpt, required_terms, data_origin, collected_at FROM phoenix.prior_art WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: 11ccfcd37962909c0fd87e156291e7e9a035f7f89f5e3b2e3b64785ed4cd982f
- F8: Prior art NapalmDeath/youtrack-outline-importer (https://github.com/NapalmDeath/youtrack-outline-importer) licence empty, stars 0, pushed 2024-07-07T12:56:57Z, 837 days before collection. Required terms: knowledge base|push changes back. Excerpt: Convert Youtrack KnowledgeBase markdown to outline format
  - evidence: EV-PRIOR-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, alternative_id, url, licence_spdx, stars, pushed_at, days_since_push, excerpt, candidate_excerpt, required_terms, data_origin, collected_at FROM phoenix.prior_art WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: 11ccfcd37962909c0fd87e156291e7e9a035f7f89f5e3b2e3b64785ed4cd982f
- F9: Prior art udamir/youtrack-markdown-snippet (https://github.com/udamir/youtrack-markdown-snippet) licence MIT, stars 4, pushed 2026-08-05T13:13:10Z, 78 days before collection. Required terms: knowledge base|push changes back. Excerpt: Youtrack widget allows to embed article/issue parts into markdown content
  - evidence: EV-PRIOR-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, alternative_id, url, licence_spdx, stars, pushed_at, days_since_push, excerpt, candidate_excerpt, required_terms, data_origin, collected_at FROM phoenix.prior_art WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: 11ccfcd37962909c0fd87e156291e7e9a035f7f89f5e3b2e3b64785ed4cd982f
- F10: Prior art siporoz/youtrack-ai-context-exporter (https://github.com/siporoz/youtrack-ai-context-exporter) licence MIT, stars 0, pushed 2026-05-06T04:39:08Z, 169 days before collection. Required terms: knowledge base|push changes back. Excerpt: Chrome extension that copies YouTrack issue context as Markdown for LLM workflows
  - evidence: EV-PRIOR-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, alternative_id, url, licence_spdx, stars, pushed_at, days_since_push, excerpt, candidate_excerpt, required_terms, data_origin, collected_at FROM phoenix.prior_art WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: 11ccfcd37962909c0fd87e156291e7e9a035f7f89f5e3b2e3b64785ed4cd982f

## Stage 1 — REVIVE

Plan only. This run does not fork or commit.

### P1

Plan only. This run does not fork the repository and does not commit. The tree already contains main.go, cmd/push.go, cmd/download.go, internal/api/client.go, internal/markdown/parser.go, internal/filesystem/files.go. Last push 2026-03-31T19:56:26Z.

Acceptance:
- No fork and no commit are produced by this plan.
- evidence: EV-DISCOVERY-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, html_url, description, readme_excerpt, problem_excerpt, user_excerpt, scope_excerpt, code_paths, created_at, pushed_at, days_since_push, stargazers_count, forks_count, open_issues, licence_spdx, core_terms, cohort, data_origin, collected_at FROM phoenix.discovery_candidates WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: ac02bd72d438940eae4e6bbb243ec44a89849595a0b00ed5036d34250de367af


## Stage 2 — REFACTOR

Plan only. This run does not fork or commit.

### P2

Plan only. Keep the source paths already in code_paths (main.go, cmd/push.go, cmd/download.go, internal/api/client.go, internal/markdown/parser.go, internal/filesystem/files.go). This plan does not rewrite them.

Acceptance:
- The plan names only paths present in code_paths.
- evidence: EV-DISCOVERY-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, html_url, description, readme_excerpt, problem_excerpt, user_excerpt, scope_excerpt, code_paths, created_at, pushed_at, days_since_push, stargazers_count, forks_count, open_issues, licence_spdx, core_terms, cohort, data_origin, collected_at FROM phoenix.discovery_candidates WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: ac02bd72d438940eae4e6bbb243ec44a89849595a0b00ed5036d34250de367af


## Stage 3 — UPGRADE

Plan only. This run does not fork or commit.

### P3

Plan only. The README excerpt records unchecked roadmap lines 'Manage images and attachments' and 'Allow page creation from new md files'. Those lines are not scheduled by this plan.

Acceptance:
- This plan does not add image upload or page creation.
- evidence: EV-DISCOVERY-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, html_url, description, readme_excerpt, problem_excerpt, user_excerpt, scope_excerpt, code_paths, created_at, pushed_at, days_since_push, stargazers_count, forks_count, open_issues, licence_spdx, core_terms, cohort, data_origin, collected_at FROM phoenix.discovery_candidates WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: ac02bd72d438940eae4e6bbb243ec44a89849595a0b00ed5036d34250de367af


## Stage 4 — PRODUCTISE

Plan only. This run does not fork or commit.

### P4

Add an SPDX licence file only after a human confirms the intended id. licence_spdx is empty. A README badge is not a licence file and is not licence_spdx.

Acceptance:
- The licence file is added only after the SPDX id is confirmed. This plan does not invent one.
- evidence: EV-DISCOVERY-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, html_url, description, readme_excerpt, problem_excerpt, user_excerpt, scope_excerpt, code_paths, created_at, pushed_at, days_since_push, stargazers_count, forks_count, open_issues, licence_spdx, core_terms, cohort, data_origin, collected_at FROM phoenix.discovery_candidates WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: ac02bd72d438940eae4e6bbb243ec44a89849595a0b00ed5036d34250de367af

### P5

Keep the install path already in the README excerpt: `go build -o ytkb` and `go install`.

Acceptance:
- The install instructions stay the commands quoted from the README excerpt.
- evidence: EV-DISCOVERY-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, html_url, description, readme_excerpt, problem_excerpt, user_excerpt, scope_excerpt, code_paths, created_at, pushed_at, days_since_push, stargazers_count, forks_count, open_issues, licence_spdx, core_terms, cohort, data_origin, collected_at FROM phoenix.discovery_candidates WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: ac02bd72d438940eae4e6bbb243ec44a89849595a0b00ed5036d34250de367af

### P6

State the user-facing scope from the README features already cited: Download all pages from a YouTrack knowledge base, preserving hierarchy and order; Diff local files with server versions; Push changes back to YouTrack (update only, no creation); Tree display of the article hierarchy; Star to add or remove stars on articles.

Acceptance:
- The user-facing scope lists those README features and does not add the unchecked roadmap items.
- evidence: EV-DISCOVERY-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, html_url, description, readme_excerpt, problem_excerpt, user_excerpt, scope_excerpt, code_paths, created_at, pushed_at, days_since_push, stargazers_count, forks_count, open_issues, licence_spdx, core_terms, cohort, data_origin, collected_at FROM phoenix.discovery_candidates WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: ac02bd72d438940eae4e6bbb243ec44a89849595a0b00ed5036d34250de367af

## Risks and unknowns

- R1: A GitHub search total_count is that query's total. It is not a census and it does not prove that no other tool exists.
  - evidence: EV-DEMAND-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, signal_id, kind, url, excerpt, metric_name, metric_value, comments, reactions, stars, pushed_at, days_since_push, query, data_origin, collected_at FROM phoenix.demand_signals WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: d60b606614a33e1f5df065bdf9392de2f350b69bc56fbdea76de3a6e59de40d1
- R2: licence_spdx empty means the API sent no SPDX id. README badge text is not a substitute for that field.
  - evidence: EV-DISCOVERY-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, html_url, description, readme_excerpt, problem_excerpt, user_excerpt, scope_excerpt, code_paths, created_at, pushed_at, days_since_push, stargazers_count, forks_count, open_issues, licence_spdx, core_terms, cohort, data_origin, collected_at FROM phoenix.discovery_candidates WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: ac02bd72d438940eae4e6bbb243ec44a89849595a0b00ed5036d34250de367af
- R3: This document is a plan. It does not record a fork, a commit, or a CI run for this repository.
  - evidence: EV-DISCOVERY-SergioMendolia-ytkb; query_id: snapshot; sql: SELECT repo_id, html_url, description, readme_excerpt, problem_excerpt, user_excerpt, scope_excerpt, code_paths, created_at, pushed_at, days_since_push, stargazers_count, forks_count, open_issues, licence_spdx, core_terms, cohort, data_origin, collected_at FROM phoenix.discovery_candidates WHERE repo_id = 'SergioMendolia/ytkb'; content_hash: ac02bd72d438940eae4e6bbb243ec44a89849595a0b00ed5036d34250de367af

## Out of scope

Non-factual framing. These limits are instructions, not cited evidence claims.

- Do not fork this repository and do not push commits as part of this plan.
- Do not treat the repository's own stars, forks, or issues as demand.
- Do not schedule the unchecked README roadmap items.
- Do not treat a search total of 0 as proof that no alternative exists.
- Do not copy a README licence badge into licence_spdx.
