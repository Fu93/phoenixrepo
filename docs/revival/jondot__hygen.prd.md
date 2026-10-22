# Revival PRD: jondot/hygen

Drafted by: deterministic. llm_drafted: false.
Run hash: not sealed; this file is the deterministic snapshot, not an investigation run
Window: Finish each stage before 27 Oct 2026 23:45 UTC.

## Problem statement

Non-factual framing. This section sets the task. It is not a cited evidence claim.

Produce an executable revival of jondot/hygen on https://github.com/Fu93/hygen default branch master. Stage 1 REVIVE makes install, build, test, and eligible default-branch CI green. That stage is the gate for RESURRECTED. Stage 2 is the cited dependency security bump. Stage 3 is one evidence-backed visible upgrade. Compare URL: https://github.com/jondot/hygen/compare/master...Fu93:master. This problem statement is task framing, not a cited evidence claim.

## Evidence-backed findings

- F1: jondot/hygen licence MIT, primary_language JavaScript, days_since_push 835, has_tests True, ci_status failure, description The simple, fast, and scalable code generator that lives in your project..
  - evidence: EV-PROFILE-jondot-hygen; query_id: snapshot; sql: SELECT repo_id, full_name, description, licence_spdx, primary_language, stars, forks, archived, days_since_push, open_issues, has_readme, has_license_file, has_tests, has_ci, ci_status, similar_repo_count, unique_capability, successor_url, category, html_url, data_origin FROM phoenix.repo_evidence WHERE repo_id = 'jondot/hygen'
- F2: Signal ci_failure: The CI toolchain setup step (Use Node.js 14.x) failed, so CI has decayed. This is not a test failure. Workflow CI event schedule. Run https://github.com/jondot/hygen/actions/runs/37141993934.
  - evidence: EV-SIGNAL-jondot-hygen-1-ci-failure; query_id: snapshot; sql: SELECT repo_id, signal, detail, supports_revival FROM phoenix.revival_signals WHERE repo_id = 'jondot/hygen'
- F3: Source workflow workflow-ci title CI url https://github.com/jondot/hygen/blob/master/.github/workflows/ci.yml. Excerpt: - cron: '0 14 3 * *' # Monthly at 2pm on the 3rd; node-version: [14.x, 17.x]; actions/checkout@v2; actions/setup-node@v2; cache: 'yarn'; yarn install; yarn build; yarn hygen:build -- init self; yarn test; yarn test:win32
  - evidence: EV-SOURCE-jondot-hygen-workflow-ci; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F4: Source package package-json title package.json url https://github.com/jondot/hygen/blob/master/package.json. Excerpt: version 6.2.11; bin hygen ./dist/bin.js; scripts.test = yarn test:code && yarn test:require; scripts.hygen:build = node dist/bin.js; scripts.build = yarn build:lib && yarn build:changelog; dependencies.ejs = ^3.1.6; dependencies.degit = ^2.8.4
  - evidence: EV-SOURCE-jondot-hygen-package-json; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F5: Source issue issue-450 title hygen --version treated as error url https://github.com/jondot/hygen/issues/450. Excerpt: ean>((resolve) => { exec('hygen --version', (error, stdout, stderr) => { if (error) { resolve(false); } else { resolve(true); } }); }); }); ``` hygen --version Error: please specify a generator. Hygen v6.2.11 Available actions: init: init repo, init self C:\Users\myname>hygen -v Error: please specify a generator. Hygen v6.2.11 Available actions: init: init repo, init self
  - evidence: EV-SOURCE-jondot-hygen-issue-450; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F6: Source issue_list issue-list title open issues first page url https://github.com/jondot/hygen/issues. Excerpt: GET /repos/jondot/hygen/issues?state=open&per_page=30 returned 30 items on the first page (21 issues, 9 pull requests). Stage 3 uses issue 450 only.
  - evidence: EV-SOURCE-jondot-hygen-issue-list; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F7: Source compare compare-fork title compare Fu93/hygen url https://github.com/jondot/hygen/compare/master...Fu93:master. Excerpt: status identical; ahead_by 0; behind_by 0; total_commits 0; base_sha 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae
  - evidence: EV-SOURCE-jondot-hygen-compare-fork; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F8: Source actions fork-actions title Fu93/hygen actions url https://github.com/Fu93/hygen/actions. Excerpt: default_branch master; actions total_count 0; eligible_conclusion empty; runs_on_default_branch 0
  - evidence: EV-SOURCE-jondot-hygen-fork-actions; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F9: Source ci_log ci-log-37141993934 title upstream CI job log url https://github.com/jondot/hygen/actions/runs/37141993934. Excerpt: Jobs API GET /repos/jondot/hygen/actions/runs/37141993934/jobs returned HTTP 200. Failure-conclusion job first failed step: Use Node.js 14.x. The upstream job log for job 111258182240 (step Use Node.js 14.x, actions/setup-node@v2 cache) contains the line 'Cache service responded with 400'. GET /repos/jondot/hygen/actions/jobs/111258182240/logs returned HTTP 403. The log archive bytes were not stored when that GET was not HTTP 200. A separate test run passed 40 tests on Node 20.x and Node 22.x. One test needs network. Windows yarn test:win32 was not run. Fork Actions must be enabled once before pushes trigger CI, and scheduled runs do not fire on forks.
  - evidence: EV-SOURCE-jondot-hygen-ci-log-37141993934; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F10: Source advisory GHSA-ghr5-ch3p-vcr6 title ejs lacks certain pollution protection url https://github.com/advisories/GHSA-ghr5-ch3p-vcr6. Excerpt: package.json declares ejs ^3.1.6. GHSA-ghr5-ch3p-vcr6 severity medium CVE-2024-33883. ejs lacks certain pollution protection. ejs vulnerable_version_range < 3.1.10 first_patched_version 3.1.10. The lockfile was not fetched, so the resolved version is not claimed here.
  - evidence: EV-SOURCE-jondot-hygen-GHSA-ghr5-ch3p-vcr6; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F11: Source advisory GHSA-phwq-j96m-2c2q title ejs template injection vulnerability url https://github.com/advisories/GHSA-phwq-j96m-2c2q. Excerpt: package.json declares ejs ^3.1.6. GHSA-phwq-j96m-2c2q severity critical CVE-2022-29078. ejs template injection vulnerability. ejs vulnerable_version_range < 3.1.7 first_patched_version 3.1.7. The lockfile was not fetched, so the resolved version is not claimed here.
  - evidence: EV-SOURCE-jondot-hygen-GHSA-phwq-j96m-2c2q; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F12: Source advisory GHSA-77c7-pq4r-6mcq title degit has a Command Injection issue url https://github.com/advisories/GHSA-77c7-pq4r-6mcq. Excerpt: package.json declares degit ^2.8.4. GHSA-77c7-pq4r-6mcq severity high CVE-2026-11572. degit has a Command Injection issue. degit vulnerable_version_range < 2.8.6 first_patched_version 2.8.6. The lockfile was not fetched, so the resolved version is not claimed here.
  - evidence: EV-SOURCE-jondot-hygen-GHSA-77c7-pq4r-6mcq; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F13: Source test_helper metaverse-utils title metaverse-utils path normalizer url https://github.com/jondot/hygen/blob/master/src/__tests__/metaverse-utils.ts. Excerpt: const normalize = (s) => s.replace(/\\/g, '/').replace(/.*hygen\/src/, '')
  - evidence: EV-SOURCE-jondot-hygen-metaverse-utils; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- F14: Stage revive status not_attempted ci_status empty ci_run_url empty commit_sha empty head_sha 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae fork Fu93/hygen default_branch master compare_url https://github.com/jondot/hygen/compare/master...Fu93:master.
  - evidence: EV-ATTEMPT-jondot-hygen-revive; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, upstream_base_sha, ci_head_repo, workflow_path, workflow_steps, workflow_runs_build, compare_shas, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'
- F15: Stage refactor status not_attempted ci_status empty ci_run_url empty commit_sha empty head_sha 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae fork Fu93/hygen default_branch master compare_url https://github.com/jondot/hygen/compare/master...Fu93:master.
  - evidence: EV-ATTEMPT-jondot-hygen-refactor; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, upstream_base_sha, ci_head_repo, workflow_path, workflow_steps, workflow_runs_build, compare_shas, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'
- F16: Stage upgrade status not_attempted ci_status empty ci_run_url empty commit_sha empty head_sha 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae fork Fu93/hygen default_branch master compare_url https://github.com/jondot/hygen/compare/master...Fu93:master.
  - evidence: EV-ATTEMPT-jondot-hygen-upgrade; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, upstream_base_sha, ci_head_repo, workflow_path, workflow_steps, workflow_runs_build, compare_shas, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'

## Stage 1 — REVIVE

Gate for RESURRECTED.

### S1-toolchain

Stage 1 toolchain: bump the cited v2 actions to v4 and use a current Node. The workflow excerpt lists Node 14.x and 17.x. Use Node 20.x. Node 22.x is optional when a cited excerpt says that version also passed. The cited upstream job log contains the line `Cache service responded with 400` inside actions/setup-node@v2 cache, plus deprecated v2 action runtimes. Do not change product behavior in this stage.

Acceptance:
- On the fork default branch, `.github/workflows/ci.yml` contains `actions/checkout@v4` and `actions/setup-node@v4`, and it does not contain `actions/checkout@v2` or `actions/setup-node@v2`.
- On the fork default branch, `.github/workflows/ci.yml` contains `20.x` and does not contain `14.x` and does not contain `17.x`.
- evidence: EV-SOURCE-jondot-hygen-workflow-ci; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- evidence: EV-SIGNAL-jondot-hygen-1-ci-failure; query_id: snapshot; sql: SELECT repo_id, signal, detail, supports_revival FROM phoenix.revival_signals WHERE repo_id = 'jondot/hygen'
- evidence: EV-SOURCE-jondot-hygen-ci-log-37141993934; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'

### S1-hygen-build

Stage 1 keeps the workflow command `yarn hygen:build -- init self` that the excerpt already runs.

Acceptance:
- `.github/workflows/ci.yml` still contains `yarn hygen:build -- init self`.
- evidence: EV-SOURCE-jondot-hygen-workflow-ci; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'

### S1-commands

Stage 1 local gate: run the install, build, and test commands named in the workflow excerpt.

Acceptance:
- `yarn install` exits 0.
- `yarn build` exits 0.
- `yarn test` exits 0.
- evidence: EV-SOURCE-jondot-hygen-workflow-ci; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'

### S1-ci

Stage 1 CI gate: push an attempt commit to fork Fu93/hygen default branch master and wait for an eligible build/test workflow run on that default branch. The run's commit must differ from the upstream base SHA, and the run must belong to the fork. A workflow that does not run the repo's build or test commands does not count. Security scans, action_required runs, and fork pull requests do not count. This stage is the only gate for RESURRECTED.

Acceptance:
- phoenix.revival_attempts has a row for this repo_id with stage revive, status success, ci_status success, a non-empty ci_run_url, a non-empty commit_sha, ci_head_repo equal to the fork, and commit_sha different from upstream_base_sha.
- workflow_steps on that row contain the repo's build or test command, and workflow_runs_build is true.
- evidence: EV-ATTEMPT-jondot-hygen-revive; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, upstream_base_sha, ci_head_repo, workflow_path, workflow_steps, workflow_runs_build, compare_shas, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'
- evidence: EV-PROFILE-jondot-hygen; query_id: snapshot; sql: SELECT repo_id, full_name, description, licence_spdx, primary_language, stars, forks, archived, days_since_push, open_issues, has_readme, has_license_file, has_tests, has_ci, ci_status, similar_repo_count, unique_capability, successor_url, category, html_url, data_origin FROM phoenix.repo_evidence WHERE repo_id = 'jondot/hygen'


## Stage 2 — REFACTOR / OPTIMISE

Upgrade result. Does not change the verdict.

### S2-advisories

Stage 2 dependency security bumps, limited to advisories in the cited revival_sources rows. GHSA-ghr5-ch3p-vcr6 at https://github.com/advisories/GHSA-ghr5-ch3p-vcr6: package.json declares ejs ^3.1.6. GHSA-ghr5-ch3p-vcr6 severity medium CVE-2024-33883. ejs lacks certain pollution protection. ejs vulnerable_version_range < 3.1.10 first_patched_version 3.1.10. The lockfile was not fetched, so the resolved version is not claimed here. GHSA-phwq-j96m-2c2q at https://github.com/advisories/GHSA-phwq-j96m-2c2q: package.json declares ejs ^3.1.6. GHSA-phwq-j96m-2c2q severity critical CVE-2022-29078. ejs template injection vulnerability. ejs vulnerable_version_range < 3.1.7 first_patched_version 3.1.7. The lockfile was not fetched, so the resolved version is not claimed here. GHSA-77c7-pq4r-6mcq at https://github.com/advisories/GHSA-77c7-pq4r-6mcq: package.json declares degit ^2.8.4. GHSA-77c7-pq4r-6mcq severity high CVE-2026-11572. degit has a Command Injection issue. degit vulnerable_version_range < 2.8.6 first_patched_version 2.8.6. The lockfile was not fetched, so the resolved version is not claimed here.

Acceptance:
- The revived tree resolves the package named in GHSA-ghr5-ch3p-vcr6 to a version outside that advisory's vulnerable range. Cite https://github.com/advisories/GHSA-ghr5-ch3p-vcr6.
- The revived tree resolves the package named in GHSA-phwq-j96m-2c2q to a version outside that advisory's vulnerable range. Cite https://github.com/advisories/GHSA-phwq-j96m-2c2q.
- The revived tree resolves the package named in GHSA-77c7-pq4r-6mcq to a version outside that advisory's vulnerable range. Cite https://github.com/advisories/GHSA-77c7-pq4r-6mcq.
- `yarn test` exits 0 after the bump, when the workflow excerpt names yarn test.
- phoenix.revival_attempts records stage refactor with status, ci_run_url, and commit_sha. That row does not change the verdict.
- evidence: EV-ATTEMPT-jondot-hygen-refactor; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, upstream_base_sha, ci_head_repo, workflow_path, workflow_steps, workflow_runs_build, compare_shas, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'
- evidence: EV-SOURCE-jondot-hygen-package-json; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- evidence: EV-SOURCE-jondot-hygen-GHSA-ghr5-ch3p-vcr6; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- evidence: EV-SOURCE-jondot-hygen-GHSA-phwq-j96m-2c2q; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- evidence: EV-SOURCE-jondot-hygen-GHSA-77c7-pq4r-6mcq; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'


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
- evidence: EV-ATTEMPT-jondot-hygen-upgrade; query_id: snapshot; sql: SELECT repo_id, fork_full_name, upstream_full_name, stage, status, ci_status, ci_run_url, ci_run_name, ci_run_event, commit_sha, compare_url, default_branch, head_sha, upstream_base_sha, ci_head_repo, workflow_path, workflow_steps, workflow_runs_build, compare_shas, data_origin FROM phoenix.revival_attempts WHERE repo_id = 'jondot/hygen'

## Risks and unknowns

- R1: Compare source https://github.com/jondot/hygen/compare/master...Fu93:master excerpt: status identical; ahead_by 0; behind_by 0; total_commits 0; base_sha 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae. An empty commit_sha means there is no attempt commit yet. Observed head_sha is 26f76d8dede565b2bf38d0b9e9f3009ee2c8f9ae and is not an attempt commit.
  - evidence: EV-SOURCE-jondot-hygen-compare-fork; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- R2: Fork Actions source excerpt: default_branch master; actions total_count 0; eligible_conclusion empty; runs_on_default_branch 0. CI stays unknown until an eligible run exists.
  - evidence: EV-SOURCE-jondot-hygen-fork-actions; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- R3: The upstream CI failure is a toolchain setup step. Do not describe it as a test failure and do not start by rewriting tests.
  - evidence: EV-SIGNAL-jondot-hygen-1-ci-failure; query_id: snapshot; sql: SELECT repo_id, signal, detail, supports_revival FROM phoenix.revival_signals WHERE repo_id = 'jondot/hygen'
- R4: Source ci_log ci-log-37141993934 excerpt: Jobs API GET /repos/jondot/hygen/actions/runs/37141993934/jobs returned HTTP 200. Failure-conclusion job first failed step: Use Node.js 14.x. The upstream job log for job 111258182240 (step Use Node.js 14.x, actions/setup-node@v2 cache) contains the line 'Cache service responded with 400'. GET /repos/jondot/hygen/actions/jobs/111258182240/logs returned HTTP 403. The log archive bytes were not stored when that GET was not HTTP 200. A separate test run passed 40 tests on Node 20.x and Node 22.x. One test needs network. Windows yarn test:win32 was not run. Fork Actions must be enabled once before pushes trigger CI, and scheduled runs do not fire on forks.
  - evidence: EV-SOURCE-jondot-hygen-ci-log-37141993934; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- R5: Source test_helper metaverse-utils excerpt: const normalize = (s) => s.replace(/\\/g, '/').replace(/.*hygen\/src/, '')
  - evidence: EV-SOURCE-jondot-hygen-metaverse-utils; query_id: snapshot; sql: SELECT repo_id, source_id, kind, title, url, excerpt, data_origin FROM phoenix.revival_sources WHERE repo_id = 'jondot/hygen'
- R6: A row that passes the policy with no qualifying stage-1 success is CANDIDATE. Only a qualifying stage revive success is RESURRECTED. A qualifying stage revive failure is RESURRECTION_FAILED. NO_GO is not overridden. Refactor and upgrade do not change the verdict.
  - evidence: EV-PROFILE-jondot-hygen; query_id: snapshot; sql: SELECT repo_id, full_name, description, licence_spdx, primary_language, stars, forks, archived, days_since_push, open_issues, has_readme, has_license_file, has_tests, has_ci, ci_status, similar_repo_count, unique_capability, successor_url, category, html_url, data_origin FROM phoenix.repo_evidence WHERE repo_id = 'jondot/hygen'
- R7: Finish each stage before 27 Oct 2026 23:45 UTC. Keep each stage to the tasks in this PRD.
  - evidence: EV-PROFILE-jondot-hygen; query_id: snapshot; sql: SELECT repo_id, full_name, description, licence_spdx, primary_language, stars, forks, archived, days_since_push, open_issues, has_readme, has_license_file, has_tests, has_ci, ci_status, similar_repo_count, unique_capability, successor_url, category, html_url, data_origin FROM phoenix.repo_evidence WHERE repo_id = 'jondot/hygen'

## Out of scope

Non-factual framing. These limits are instructions, not cited evidence claims.

- Do not invent GitHub issues, CI conclusions, commit SHAs, test failures, or advisories.
- Do not count a security or analysis scan, an action_required run, or a fork pull request as CI success.
- Do not publish a release or rewrite the product.
- Do not switch the package manager unless the workflow excerpt names that manager.
- Do not implement any open issue other than https://github.com/jondot/hygen/issues/450.
- Do not replace yarn with npm. The workflow excerpt names yarn.
