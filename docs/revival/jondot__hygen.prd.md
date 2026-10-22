# Revival PRD: jondot/hygen

Drafted by: deterministic. llm_drafted: false.
Window: Finish each stage before 27 Oct 2026 23:45 UTC.

## Problem statement

Produce an executable revival of jondot/hygen on https://github.com/Fu93/hygen default branch master. Stage 1 REVIVE makes install, build, test, and eligible default-branch CI green. That stage is the gate for RESURRECTED. Stage 2 is one evidence-backed cleanup. Stage 3 is one evidence-backed visible upgrade. Compare URL: https://github.com/jondot/hygen/compare/master...Fu93:master. No LLM drafted this text.

## Evidence-backed findings

- F1: jondot/hygen licence MIT, days_since_push 835, has_tests True, ci_status failure, description The simple, fast, and scalable code generator that lives in your project..
  - evidence: EV-PROFILE-jondot-hygen; query_id: snapshot; sql: SELECT repo_id, full_name, description, licence_spdx, primary_language, stars, forks, archived, days_since_push, open_issues, has_readme, has_license_file, has_tests, has_ci, ci_status, similar_repo_count, unique_capability, successor_url, category, html_url, data_origin FROM phoenix.repo_evidence WHERE repo_id = 'jondot/hygen'
- F2: Signal ci_failure: The CI toolchain setup step (Use Node.js 14.x) failed, so CI has decayed. This is not a test failure. Workflow CI event schedule. Run https://github.com/jondot/hygen/actions/runs/37141993934.
  - evidence: EV-SIGNAL-jondot-hygen-1-ci-failure; query_id: snapshot; sql: SELECT repo_id, signal, detail, supports_revival FROM phoenix.revival_signals WHERE repo_id = 'jondot/hygen'
- F3: Source workflow workflow-ci title CI url https://github.com/jondot/hygen/blob/master/.github/workflows/ci.yml. Excerpt: node-version: [14.x, 17.x]; actions/checkout@v2; actions/setup-node@v2; yarn install; yarn build; yarn test
  - evidence: EV-SOURCE-jondot-hygen-workflow-ci; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F4: Source package package-json title package.json url https://github.com/jondot/hygen/blob/master/package.json. Excerpt: version 6.2.11; bin hygen ./dist/bin.js; scripts.test = yarn test:code && yarn test:require; scripts.hygen:build = node dist/bin.js
  - evidence: EV-SOURCE-jondot-hygen-package-json; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F5: Source issue issue-450 title hygen --version treated as error url https://github.com/jondot/hygen/issues/450. Excerpt: ean>((resolve) => { exec('hygen --version', (error, stdout, stderr) => { if (error) { resolve(false); } else { resolve(true); } }); }); }); ``` hygen --version Error: please specify a generator. Hygen v6.2.11 Available actions: init: init repo, init self C:\Users\myname>hygen -v Error: please specify a generator. Hygen v6.2.11 Available actions: init: init repo, init self
  - evidence: EV-SOURCE-jondot-hygen-issue-450; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F6: Source issue_list issue-list title open issues first page url https://github.com/jondot/hygen/issues. Excerpt: GET /repos/jondot/hygen/issues?state=open&per_page=30 returned 30 items on the first page (21 issues, 9 pull requests). Stage 3 uses issue 450 only.
  - evidence: EV-SOURCE-jondot-hygen-issue-list; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F7: Source compare compare-fork title compare Fu93/hygen url https://github.com/jondot/hygen/compare/master...Fu93:master. Excerpt: status identical; ahead_by 0; behind_by 0; total_commits 0; base_sha 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae
  - evidence: EV-SOURCE-jondot-hygen-compare-fork; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F8: Source actions fork-actions title Fu93/hygen actions url https://github.com/Fu93/hygen/actions. Excerpt: default_branch master; actions total_count 0; eligible_conclusion empty; runs_on_default_branch 0
  - evidence: EV-SOURCE-jondot-hygen-fork-actions; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F9: Stage revive status not_attempted ci_status empty ci_run_url empty commit_sha empty head_sha 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae fork Fu93/hygen default_branch master compare_url https://github.com/jondot/hygen/compare/master...Fu93:master.
  - evidence: EV-ATTEMPT-jondot-hygen-revive; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'
- F10: Stage refactor status not_attempted ci_status empty ci_run_url empty commit_sha empty head_sha 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae fork Fu93/hygen default_branch master compare_url https://github.com/jondot/hygen/compare/master...Fu93:master.
  - evidence: EV-ATTEMPT-jondot-hygen-refactor; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'
- F11: Stage upgrade status not_attempted ci_status empty ci_run_url empty commit_sha empty head_sha 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae fork Fu93/hygen default_branch master compare_url https://github.com/jondot/hygen/compare/master...Fu93:master.
  - evidence: EV-ATTEMPT-jondot-hygen-upgrade; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'

## Stage 1 — REVIVE

Gate for RESURRECTED.

### S1-toolchain

Stage 1 toolchain: the workflow excerpt lists Node 14.x and 17.x. The recorded CI blocker is a toolchain setup step, not a test failure. Change the workflow so the eligible CI job no longer requests those Node versions. Use one Node version actions/setup-node can install. Do not change product behavior in this stage.

Acceptance:
- On the fork default branch, `.github/workflows/ci.yml` does not contain `14.x` and does not contain `17.x`.
- evidence: EV-SOURCE-jondot-hygen-workflow-ci; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- evidence: EV-SIGNAL-jondot-hygen-1-ci-failure; query_id: snapshot; sql: SELECT repo_id, signal, detail, supports_revival FROM phoenix.revival_signals WHERE repo_id = 'jondot/hygen'

### S1-commands

Stage 1 local gate: run the install, build, and test commands named in the workflow excerpt.

Acceptance:
- `yarn install` exits 0.
- `yarn build` exits 0.
- `yarn test` exits 0.
- evidence: EV-SOURCE-jondot-hygen-workflow-ci; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'

### S1-ci

Stage 1 CI gate: push the attempt commit to fork Fu93/hygen default branch master and wait for an eligible build/test workflow run on that default branch. Security scans, action_required runs, and fork pull requests do not count. This stage is the only gate for RESURRECTED.

Acceptance:
- phoenix.revival_attempts has a row for this repo_id with stage revive, status success, ci_status success, a non-empty ci_run_url, and a non-empty commit_sha.
- That ci_run_url is a build/test workflow run on the fork default branch, not a security scan.
- evidence: EV-ATTEMPT-jondot-hygen-revive; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'
- evidence: EV-PROFILE-jondot-hygen; query_id: snapshot; sql: SELECT repo_id, full_name, description, licence_spdx, primary_language, stars, forks, archived, days_since_push, open_issues, has_readme, has_license_file, has_tests, has_ci, ci_status, similar_repo_count, unique_capability, successor_url, category, html_url, data_origin FROM phoenix.repo_evidence WHERE repo_id = 'jondot/hygen'


## Stage 2 — REFACTOR / OPTIMISE

Upgrade result. Does not change the verdict.

### S2-action-pins

Stage 2 refactor: the workflow excerpt pins actions/checkout@v2 and actions/setup-node@v2. Replace those action pins. Do not add a second product change in this stage.

Acceptance:
- On the fork default branch, `.github/workflows/ci.yml` does not contain `actions/checkout@v2` and does not contain `actions/setup-node@v2`.
- `yarn test` exits 0.
- phoenix.revival_attempts records stage refactor with status, ci_run_url, and commit_sha. That row does not change the verdict.
- evidence: EV-SOURCE-jondot-hygen-workflow-ci; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- evidence: EV-ATTEMPT-jondot-hygen-refactor; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'


## Stage 3 — UPGRADE

Upgrade result. Does not change the verdict.

### S3-issue

Stage 3 upgrade: hygen --version treated as error at https://github.com/jondot/hygen/issues/450. The issue excerpt shows the version command printing the generator error before the version marker. Change that command so the checks below pass. Do not take any other open issue in this stage.

Acceptance:
- `node dist/bin.js --version` exits 0.
- stdout contains `Hygen v`.
- stdout does not contain `Error: please specify a generator.`
- `node dist/bin.js -v` exits 0 and follows the same stdout checks.
- phoenix.revival_attempts records stage upgrade with status, ci_run_url, and commit_sha. That row does not change the verdict.
- evidence: EV-SOURCE-jondot-hygen-issue-450; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- evidence: EV-SOURCE-jondot-hygen-package-json; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- evidence: EV-ATTEMPT-jondot-hygen-upgrade; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'

## Risks and unknowns

- R1: Compare source https://github.com/jondot/hygen/compare/master...Fu93:master excerpt: status identical; ahead_by 0; behind_by 0; total_commits 0; base_sha 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae. An empty commit_sha means there is no attempt commit yet. Observed head_sha is 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae and is not an attempt commit.
  - evidence: EV-SOURCE-jondot-hygen-compare-fork; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- R2: Fork Actions source excerpt: default_branch master; actions total_count 0; eligible_conclusion empty; runs_on_default_branch 0. CI stays unknown until an eligible run exists.
  - evidence: EV-SOURCE-jondot-hygen-fork-actions; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- R3: The upstream CI failure is a toolchain setup step. Do not describe it as a test failure and do not start by rewriting tests.
  - evidence: EV-SIGNAL-jondot-hygen-1-ci-failure; query_id: snapshot; sql: SELECT repo_id, signal, detail, supports_revival FROM phoenix.revival_signals WHERE repo_id = 'jondot/hygen'
- R4: Stages refactor and upgrade are an upgrade result. Only stage revive success or failure changes RESURRECTED or RESURRECTION_FAILED. NO_GO is not overridden by a fork.
  - evidence: EV-PROFILE-jondot-hygen; query_id: snapshot; sql: SELECT repo_id, full_name, description, licence_spdx, primary_language, stars, forks, archived, days_since_push, open_issues, has_readme, has_license_file, has_tests, has_ci, ci_status, similar_repo_count, unique_capability, successor_url, category, html_url, data_origin FROM phoenix.repo_evidence WHERE repo_id = 'jondot/hygen'
- R5: Finish each stage before 27 Oct 2026 23:45 UTC. Keep each stage to the tasks in this PRD.
  - evidence: EV-PROFILE-jondot-hygen; query_id: snapshot; sql: SELECT repo_id, full_name, description, licence_spdx, primary_language, stars, forks, archived, days_since_push, open_issues, has_readme, has_license_file, has_tests, has_ci, ci_status, similar_repo_count, unique_capability, successor_url, category, html_url, data_origin FROM phoenix.repo_evidence WHERE repo_id = 'jondot/hygen'

## Out of scope

- Do not invent GitHub issues, CI conclusions, commit SHAs, or test failures.
- Do not count a security or analysis scan, an action_required run, or a fork pull request as CI success.
- Do not publish a release or rewrite the product.
- Do not switch the package manager unless the workflow excerpt names that manager.
- Do not implement any open issue other than https://github.com/jondot/hygen/issues/450.
- Do not replace yarn with npm. The workflow excerpt names yarn.
