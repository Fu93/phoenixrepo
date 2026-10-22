"""Deterministic hygen upgrade plan.

The collector snapshot is the only input. Versions, advisory ids, reaction
counts, and CI conclusions are copied from that snapshot. A result that was
not observed is labelled hypothesis.
"""

from __future__ import annotations

import json
import re
from typing import Any

TOOLCHAIN = ("typescript", "ts-jest", "ts-node", "@types/jest", "jest")
ISSUE_LIMIT = 3
AGENT_BRANCH = "cursor/revive-hygen-326e"

_STATUS = re.compile(r"\bdead\b|still maintained|kindly asking", re.IGNORECASE)
_GOVERNANCE = re.compile(r"maintainer", re.IGNORECASE)
_NOT_CODE = re.compile(r"discussions feature|domain is expired|vs code extension", re.IGNORECASE)
_UNSURE = re.compile(r"not really sure", re.IGNORECASE)
_HOOKS = ("afterall", "beforeall", "beforeeach", "aftereach")


def version_key(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in str(value).split("."):
        digits = ""
        for character in part:
            if character.isdigit():
                digits += character
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def patched_floor(patched: str) -> str | None:
    text = str(patched or "").strip()
    if text.startswith(">="):
        floor = text[2:].strip()
        return floor or None
    return None


def in_range(version: str, spec: str) -> bool | None:
    """True when ``version`` sits in a simple space-separated npm range."""

    checks = []
    for part in str(spec or "").split():
        if part.startswith(">="):
            floor = part[2:]
            checks.append(lambda candidate, bound=floor: version_key(candidate) >= version_key(bound))
        elif part.startswith("<="):
            ceiling = part[2:]
            checks.append(lambda candidate, bound=ceiling: version_key(candidate) <= version_key(bound))
        elif part.startswith(">"):
            floor = part[1:]
            checks.append(lambda candidate, bound=floor: version_key(candidate) > version_key(bound))
        elif part.startswith("<"):
            ceiling = part[1:]
            checks.append(lambda candidate, bound=ceiling: version_key(candidate) < version_key(bound))
        else:
            return None
    if not checks:
        return None
    return all(check(version) for check in checks)


def _outdated_index(snapshot: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {row["package"]: row for row in snapshot["evidence"]["EV-OUTDATED"]["rows"]}


def _cite(snapshot: dict[str, Any], *evidence_ids: str) -> list[dict[str, str]]:
    return [
        {"evidence_id": evidence_id, "content_hash": snapshot["content_hashes"][evidence_id]}
        for evidence_id in evidence_ids
    ]


def resolution_plan(advisories: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group production advisories into a floor that satisfies every ``>=`` patch."""

    grouped: dict[str, dict[str, Any]] = {}
    for advisory in advisories:
        module = str(advisory["module"])
        bucket = grouped.setdefault(module, {"module": module, "advisories": [], "floors": [], "unfixed": []})
        bucket["advisories"].append(advisory)
        floor = patched_floor(str(advisory.get("patched") or ""))
        if floor is None:
            bucket["unfixed"].append(advisory)
        else:
            bucket["floors"].append(floor)
    fixed = []
    unfixed = []
    for bucket in grouped.values():
        if bucket["unfixed"] or not bucket["floors"]:
            unfixed.append(bucket)
            continue
        floor = max(bucket["floors"], key=version_key)
        fixed.append({**bucket, "floor": floor})
    fixed.sort(key=lambda item: item["module"])
    unfixed.sort(key=lambda item: item["module"])
    return fixed, unfixed


def classify_issue(issue: dict[str, Any], *, build_succeeded: bool) -> str:
    """Return a skip reason, or an empty string when the issue is a few-hour candidate."""

    title = str(issue.get("title") or "")
    body = str(issue.get("body") or "")
    if _GOVERNANCE.search(title):
        return "governance: the title is about maintainers, not a code change"
    if _STATUS.search(title):
        return "status question: the title asks whether the project is alive"
    if _NOT_CODE.search(title):
        return "not a code change in this repository"
    if title.rstrip().endswith("?") or title.lower().startswith("how to"):
        return "question: the title does not specify a change"
    if _UNSURE.search(body):
        return "unspecified: the body says the end of the edit is not known"
    lowered = body.lower()
    if all(hook in lowered for hook in _HOOKS):
        return "wider than one commit: the body names afterAll, beforeAll, beforeEach, and afterEach"
    if "build fails" in title.lower() and build_succeeded:
        return "not a remaining failure: the cited CI jobs already ran yarn build successfully"
    if "```" not in body:
        return "no fenced expected behavior in the body"
    return ""


def select_issues(snapshot: dict[str, Any]) -> dict[str, Any]:
    build_succeeded = _build_succeeded(snapshot)
    ranked = sorted(
        snapshot["evidence"]["EV-ISSUES"]["issues"],
        key=lambda issue: (-int(issue["reactions_total"]), -int(issue["comments"]), int(issue["number"])),
    )
    chosen = []
    skipped = []
    next_eligible = None
    for issue in ranked:
        reason = classify_issue(issue, build_succeeded=build_succeeded)
        if reason:
            if len(chosen) < ISSUE_LIMIT:
                skipped.append({"issue": issue, "reason": reason})
            continue
        if len(chosen) < ISSUE_LIMIT:
            chosen.append(issue)
            continue
        next_eligible = issue
        break
    commit_order = sorted(chosen, key=lambda issue: (0 if "diff --git" in issue["body"] else 1, len(issue["body"]), issue["number"]))
    return {"ranked_count": len(ranked), "chosen": chosen, "commit_order": commit_order, "skipped": skipped, "next_eligible": next_eligible}


def _build_succeeded(snapshot: dict[str, Any]) -> bool:
    jobs = snapshot["evidence"]["EV-CI"]["jobs"]
    if not jobs:
        return False
    for job in jobs:
        build_steps = [step for step in job["steps"] if step["name"] == "Run yarn build"]
        if not build_steps or any(step["conclusion"] != "success" for step in build_steps):
            return False
    return True


def _copies_inside(snapshot: dict[str, Any], module: str, spec: str) -> list[dict[str, str]]:
    found = []
    for copy in snapshot["evidence"]["EV-SNIPPETS"]["installed"]:
        if copy["name"] != module:
            continue
        if in_range(copy["version"], spec) is True:
            found.append(copy)
    return found


def _docs_publish_sentence(snapshot: dict[str, Any]) -> str:
    script = str(snapshot["evidence"]["EV-PACKAGE"]["scripts"].get("docs:publish") or "")
    mentioned = any("docs:publish" in item["text"] for item in snapshot["evidence"]["EV-WORKFLOW"]["lines"])
    if script and not mentioned:
        return (
            f"package.json scripts.docs:publish is `{script}`, and the extracted workflow lines do not mention docs:publish."
        )
    if mentioned:
        return "The extracted workflow lines mention docs:publish."
    return "The extracted workflow lines do not mention docs:publish."


def _workflow_line(snapshot: dict[str, Any], needle: str) -> str:
    matches = [item for item in snapshot["evidence"]["EV-WORKFLOW"]["lines"] if needle in item["text"]]
    if not matches:
        return ""
    preferred = [item for item in matches if "run:" in item["text"] or "node-version:" in item["text"] or item["text"].strip().startswith("os:")]
    item = (preferred or matches)[0]
    return f".github/workflows/ci.yml:{item['line']} `{item['text'].strip()}`"


def _prompt(snapshot: dict[str, Any], task: dict[str, Any]) -> str:
    commit = snapshot["commit"]
    ci = snapshot["evidence"]["EV-CI"]["html_url"]
    hypothesis = task["hypothesis"] or "None beyond the CI result of this commit, which has not been observed."
    return (
        f"Work on https://github.com/Fu93/hygen branch {snapshot['agent_branch']} "
        f"starting from commit {commit}. Do not reset that commit. "
        f"The green CI run is {ci} (jobs build (20.x, ubuntu-latest) and build (22.x, ubuntu-latest), conclusion success). "
        f"Keep those two ubuntu jobs green.\n\n"
        f"Task {task['id']} — {task['title']}\n"
        f"Change: {task['change']}\n"
        f"Acceptance command that runs in CI: {task['acceptance']}\n"
        f"Risk: {task['risk']}\n"
        f"Hypothesis: {hypothesis}\n"
        "Do not add network calls to the test suite. Do not bump ejs or degit in this commit."
    )


def _task(
    snapshot: dict[str, Any],
    *,
    title: str,
    change: str,
    acceptance: str,
    risk: str,
    hypothesis: str,
    evidence_ids: list[str],
    cited_versions: list[str],
    packages: list[str],
    issue_number: int | None = None,
) -> dict[str, Any]:
    blob = json.dumps(snapshot)
    missing = [version for version in cited_versions if version not in blob]
    if missing:
        raise ValueError(f"ungrounded versions: {missing}")
    task = {
        "title": title,
        "change": change,
        "acceptance": acceptance,
        "risk": risk,
        "hypothesis": hypothesis,
        "evidence_ids": evidence_ids,
        "citations": _cite(snapshot, *evidence_ids),
        "cited_versions": cited_versions,
        "packages": packages,
        "issue_number": issue_number,
    }
    return task


def _toolchain_task(snapshot: dict[str, Any]) -> dict[str, Any]:
    rows = _outdated_index(snapshot)
    bumps = []
    held = []
    for name in TOOLCHAIN:
        row = rows[name]
        if row["current"] == row["wanted"]:
            held.append(row)
            continue
        if version_key(row["wanted"])[0] != version_key(row["current"])[0]:
            continue
        bumps.append(row)
    bump_text = ", ".join(f"{row['package']} {row['current']} to {row['wanted']} (latest {row['latest']})" for row in bumps)
    held_text = ", ".join(f"{row['package']} current {row['current']} equals wanted {row['wanted']} (latest {row['latest']})" for row in held)
    versions = [value for row in bumps + held for value in (row["current"], row["wanted"], row["latest"])]
    return _task(
        snapshot,
        title="Same-major TypeScript toolchain bump",
        change=(
            "In one commit, move only the wanted versions that stay on the same major: "
            f"{bump_text}. Leave {held_text} unchanged in this commit. "
            "Do not move typescript, ts-jest, @types/jest, or jest to the latest major."
        ),
        acceptance=(
            "On the existing ubuntu-latest matrix, the steps `yarn build` and `yarn test` exit 0 for Node 20.x and Node 22.x."
        ),
        risk=(
            "typescript 4.9.5 can reject types that 4.5.5 accepted. "
            "The wanted jest line stays 27.5.1, so this commit does not take the jest 30 upgrade."
        ),
        hypothesis=(
            f"These {len(bumps)} bumps were not installed for this plan. "
            "A green `yarn test` after the bump is a hypothesis until CI on this commit says so."
        ),
        evidence_ids=["EV-OUTDATED", "EV-PACKAGE", "EV-CI"],
        cited_versions=versions,
        packages=[row["package"] for row in bumps],
    )


def _resolution_task(snapshot: dict[str, Any]) -> dict[str, Any]:
    fixed, unfixed = resolution_plan(snapshot["evidence"]["EV-AUDIT-PROD"]["advisories"])
    parts = []
    versions = []
    packages = []
    for bucket in fixed:
        ghsas = ", ".join(item["ghsa"] for item in bucket["advisories"])
        inside = []
        for advisory in bucket["advisories"]:
            for copy in _copies_inside(snapshot, bucket["module"], str(advisory["vulnerable"])):
                label = f"{copy['version']} at {copy['path']}"
                if label not in inside:
                    inside.append(label)
        where = f" Installed copies inside a cited vulnerable range: {', '.join(inside)}." if inside else ""
        parts.append(f"{bucket['module']}@{bucket['floor']} ({ghsas}).{where}")
        versions.append(bucket["floor"])
        packages.append(bucket["module"])
        for advisory in bucket["advisories"]:
            versions.extend([str(advisory["vulnerable"]), str(advisory["patched"])])
    unfixed_text = []
    for bucket in unfixed:
        for advisory in bucket["unfixed"]:
            unfixed_text.append(
                f"{advisory['module']} {advisory['ghsa']} patched {advisory['patched']} (vulnerable {advisory['vulnerable']})"
            )
            versions.extend([str(advisory["patched"]), str(advisory["vulnerable"])])
    examples = snapshot["evidence"]["EV-AUDIT-FULL"]["js_yaml_4x"]
    example_roots = sorted({str(item["example_path"]).split(">")[0] for item in examples if item.get("example_path")})
    return _task(
        snapshot,
        title="Production resolutions for advisories that have a patched release",
        change=(
            "Add yarn resolutions, and nothing else, at the strictest patched floor of each production module that has one: "
            + " ".join(parts)
            + " Do not add a resolution for "
            + "; ".join(unfixed_text)
            + ". That patched value has no release. Do not claim this commit clears the full-tree audit."
        ),
        acceptance=(
            "The existing ubuntu-latest steps `yarn build` and `yarn test` exit 0 for Node 20.x and Node 22.x. "
            "`yarn audit --groups dependencies` is not a step in the cited workflow, so it is not the CI gate."
        ),
        risk=(
            "Yarn 1 resolutions match by package name and can move copies that are outside the production ranges, "
            "including the installed js-yaml 4.1.0 copies under eslint. "
            f"The reduced audit stores {len(examples)} js-yaml 4.x rows; their example paths start at {', '.join(example_roots)}. "
            "Those rows are not part of the 20-row production audit."
        ),
        hypothesis=(
            "The resolutions were not installed and the audit was not re-run. "
            "It is a hypothesis that the production audit then omits the listed GHSA ids and that `yarn test` still exits 0."
        ),
        evidence_ids=["EV-AUDIT-PROD", "EV-AUDIT-FULL", "EV-SNIPPETS", "EV-WORKFLOW"],
        cited_versions=versions + ["4.1.0"],
        packages=packages,
    )


def _gh_pages_task(snapshot: dict[str, Any]) -> dict[str, Any]:
    row = _outdated_index(snapshot)["gh-pages"]
    advisory = next(item for item in snapshot["evidence"]["EV-AUDIT-FULL"]["direct_advisories"] if item["module"] == "gh-pages")
    floor = patched_floor(advisory["patched"])
    if floor is None or in_range(row["wanted"], advisory["vulnerable"]) is not True:
        raise ValueError("gh-pages wanted release is not inside the cited vulnerable range")
    return _task(
        snapshot,
        title="Bump gh-pages to a release inside its patched range",
        change=(
            f"gh-pages current {row['current']}, wanted {row['wanted']}, latest {row['latest']}. "
            f"{advisory['ghsa']} is vulnerable {advisory['vulnerable']} and patched {advisory['patched']}. "
            f"The wanted release {row['wanted']} is still inside {advisory['vulnerable']}, so the wanted bump is not the fix. "
            f"Pin a release that satisfies {advisory['patched']}. Latest observed is {row['latest']}; this plan does not require that exact latest. "
            "gh-pages is a devDependency. "
            + _docs_publish_sentence(snapshot)
        ),
        acceptance=(
            "The existing ubuntu-latest steps `yarn build` and `yarn test` exit 0 for Node 20.x and Node 22.x."
        ),
        risk=(
            f"A jump from {row['current']} to a {floor} release can change docs:publish. {_docs_publish_sentence(snapshot)}"
        ),
        hypothesis=(
            f"No gh-pages release at or above {floor} was installed for this plan. "
            "A later audit that no longer reports this GHSA, and a working docs publish, are hypotheses."
        ),
        evidence_ids=["EV-OUTDATED", "EV-AUDIT-FULL", "EV-PACKAGE", "EV-WORKFLOW"],
        cited_versions=[row["current"], row["wanted"], row["latest"], advisory["vulnerable"], advisory["patched"], floor],
        packages=["gh-pages"],
    )


def _issue_task(snapshot: dict[str, Any], issue: dict[str, Any]) -> dict[str, Any]:
    number = int(issue["number"])
    demand = (
        f"#{number} {issue['html_url']} reactions {issue['reactions_total']} "
        f"(+1 {issue['reactions_plus_one']}) comments {issue['comments']} updated {issue['updated_at']}"
    )
    if number == 411:
        logger = snapshot["evidence"]["EV-SNIPPETS"]["logger"]
        chalk = snapshot["evidence"]["EV-SNIPPETS"]["chalk"]
        change = (
            f"{demand}. The body points at a patch of dist/logger.js that loads `chalk/source/templates`. "
            f"{logger['path']}:{logger['line']} still destructures `template` from chalk. "
            f"Change the TypeScript source, not only dist/logger.js. "
            f"{chalk['templates_path']} exists at chalk {chalk['version']}. "
            "Do not bump chalk: yarn outdated shows current equal to wanted."
        )
        versions = [chalk["version"], _outdated_index(snapshot)["chalk"]["latest"]]
        packages = ["chalk"]
        risk = "The issue's diff targets dist/logger.js. Shipping only that file would be overwritten by the next `yarn build`."
        hypothesis = "The name of the new test is a hypothesis. The issue does not name a test."
        evidence = ["EV-ISSUES", "EV-SNIPPETS", "EV-OUTDATED"]
    elif number == 96:
        fixme = snapshot["evidence"]["EV-SNIPPETS"]["inject_fixme"]
        change = (
            f"{demand}. The body wants `dependencies = A,B` to become `dependencies = A,B,C` on the same line, "
            "not a new line. The issue does not name a frontmatter key. "
            f"Add the assertion in {snapshot['evidence']['EV-SNIPPETS']['injector_spec']}. "
            f"{fixme['path']}:{fixme['line']} `{fixme['text']}`."
        )
        versions = []
        packages = []
        risk = "A new frontmatter key can change inject output for templates that already use inject."
        hypothesis = "The frontmatter key name is a hypothesis. The issue does not name one."
        evidence = ["EV-ISSUES", "EV-SNIPPETS"]
    elif number == 396:
        change = (
            f"{demand}. The body wants one generator run over the array ['john', 'house', 'car'] "
            "to write app/john/handler.js, app/house/handler.js, and app/car/handler.js "
            "without copying the template. The issue does not name a frontmatter key or a loop syntax."
        )
        versions = []
        packages = []
        risk = "Choosing a syntax the issue does not name can miss the behavior the comments expect."
        hypothesis = "The template syntax that drives the loop is a hypothesis."
        evidence = ["EV-ISSUES"]
    else:
        raise ValueError(f"no task text for issue {number}")
    return _task(
        snapshot,
        title=f"Issue #{number}: {issue['title']}",
        change=change,
        acceptance=(
            "The existing ubuntu-latest steps `yarn build` and `yarn test` exit 0 for Node 20.x and Node 22.x, "
            "including a new test that encodes the behavior quoted from the issue."
        ),
        risk=risk,
        hypothesis=hypothesis,
        evidence_ids=evidence,
        cited_versions=versions,
        packages=packages,
        issue_number=number,
    )


def _windows_task(snapshot: dict[str, Any]) -> dict[str, Any]:
    skipped = []
    for job in snapshot["evidence"]["EV-CI"]["jobs"]:
        for step in job["steps"]:
            if step["conclusion"] == "skipped" and "test:win32" in step["name"]:
                skipped.append(f"{job['name']} skipped `{step['name']}`")
    metaverse = snapshot["evidence"]["EV-SNIPPETS"]["metaverse"]
    user = snapshot["evidence"]["EV-PROVENANCE"]["github_user"]
    return _task(
        snapshot,
        title="Add windows-latest and run yarn test:win32",
        change=(
            "The cited workflow matrix os line is "
            f"{_workflow_line(snapshot, 'os:')} and the win32 step is {_workflow_line(snapshot, 'test:win32')}. "
            + " ".join(skipped)
            + ". Add windows-latest to the os matrix so the existing gated step runs. "
            "Do not mark the job as passed in the pull request text until a windows-latest job conclusion is success."
        ),
        acceptance=(
            "A job `build (20.x, windows-latest)` runs the step `yarn test:win32` and that job's conclusion is success."
        ),
        risk=(
            f"{metaverse['path']}:{metaverse['line']} rewrites backslashes and then applies `/.*hygen\\/src/`. "
            "The workflow comment names the ubuntu checkout `/home/runner/work/hygen/hygen`. "
            "A windows checkout path was not observed."
        ),
        hypothesis=(
            "Whether `yarn test:win32` passes on windows-latest is a hypothesis. "
            f"GET /user returned HTTP {user['status']} ({user['message']}), so this collection did not push a throwaway branch to observe it."
        ),
        evidence_ids=["EV-CI", "EV-WORKFLOW", "EV-SNIPPETS", "EV-PROVENANCE"],
        cited_versions=[],
        packages=[],
    )


def _major_task(snapshot: dict[str, Any]) -> dict[str, Any]:
    rows = _outdated_index(snapshot)
    jumps = []
    for name in TOOLCHAIN:
        row = rows[name]
        if version_key(row["latest"])[0] != version_key(row["current"])[0]:
            jumps.append(f"{row['package']} {row['current']} to {row['latest']}")
    roots = snapshot["evidence"]["EV-AUDIT-FULL"]["high_critical_by_root"]
    versions = []
    for name in TOOLCHAIN:
        row = rows[name]
        versions.extend([row["current"], row["latest"]])
    return _task(
        snapshot,
        title="Major TypeScript and Jest bump",
        change=(
            "In its own last commit, the latest majors are: "
            + "; ".join(jumps)
            + f". The full audit has {roots.get('jest', 0)} high or critical rows whose resolution path starts at jest "
            f"and {roots.get('stylomatic', 0)} whose path starts at stylomatic. "
            "Do not claim either count drops until `yarn audit` is re-run."
        ),
        acceptance=(
            "The existing ubuntu-latest steps `yarn build` and `yarn test` exit 0 for Node 20.x and Node 22.x."
        ),
        risk="Jest 30 and TypeScript 7 are separate majors from the versions CI already ran. Landing them earlier would block the smaller commits if the suite goes red.",
        hypothesis="Neither latest major was installed for this plan. A green suite, and any change in the advisory counts, is a hypothesis.",
        evidence_ids=["EV-OUTDATED", "EV-AUDIT-FULL", "EV-CI"],
        cited_versions=versions,
        packages=[name for name in TOOLCHAIN if version_key(rows[name]["latest"])[0] != version_key(rows[name]["current"])[0]],
    )


def _findings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = snapshot["evidence"]
    package = evidence["EV-PACKAGE"]
    outdated = evidence["EV-OUTDATED"]["rows"]
    full = evidence["EV-AUDIT-FULL"]
    prod = evidence["EV-AUDIT-PROD"]
    ci = evidence["EV-CI"]
    issues = evidence["EV-ISSUES"]
    pr = evidence["EV-PR"]
    prov = evidence["EV-PROVENANCE"]
    lines = []

    def add(text: str, *ids: str) -> None:
        lines.append({"text": text, "evidence_ids": list(ids), "citations": _cite(snapshot, *ids)})

    scripts = package["scripts"]
    deps = package["dependencies"]
    add(
        (
            f"package.json version {package['version']} at commit {snapshot['commit']}. "
            f"dependencies ejs {deps['ejs']}, degit {deps['degit']}. "
            f"scripts.test is `{scripts['test']}`, scripts.test:win32 is `{scripts['test:win32']}`, "
            f"scripts.build is `{scripts['build']}`."
        ),
        "EV-PACKAGE",
        "EV-PROVENANCE",
    )
    rendered = "; ".join(
        f"{row['package']} current {row['current']} wanted {row['wanted']} latest {row['latest']} ({row['package_type']})"
        for row in outdated
    )
    add(
        f"`yarn outdated --json` exited {prov['commands']['outdated']['exit_code']} and listed {len(outdated)} packages: {rendered}.",
        "EV-OUTDATED",
        "EV-PROVENANCE",
    )
    vulns = full["summary"]["vulnerabilities"]
    add(
        (
            f"`yarn audit --json` exited {prov['commands']['audit_full']['exit_code']} "
            f"with {full['advisory_rows']} auditAdvisory rows. "
            f"vulnerabilities info {vulns['info']}, low {vulns['low']}, moderate {vulns['moderate']}, "
            f"high {vulns['high']}, critical {vulns['critical']}; "
            f"totalDependencies {full['summary']['totalDependencies']}."
        ),
        "EV-AUDIT-FULL",
        "EV-PROVENANCE",
    )
    prod_vulns = prod["summary"]["vulnerabilities"]
    advisory_text = "; ".join(
        f"{item['ghsa']} {item['severity']} {item['module']} vulnerable {item['vulnerable']} patched {item['patched']} path {item['path']}"
        for item in prod["advisories"]
    )
    add(
        (
            f"`yarn audit --groups dependencies --json` exited {prov['commands']['audit_prod']['exit_code']}. "
            f"vulnerabilities info {prod_vulns['info']}, low {prod_vulns['low']}, moderate {prod_vulns['moderate']}, "
            f"high {prod_vulns['high']}, critical {prod_vulns['critical']}; "
            f"dependencies {prod['summary']['dependencies']}; {len(prod['advisories'])} advisories. {advisory_text}."
        ),
        "EV-AUDIT-PROD",
        "EV-PROVENANCE",
    )
    roots = ", ".join(f"{name} {count}" for name, count in sorted(full["high_critical_by_root"].items(), key=lambda item: (-item[1], item[0])))
    add(
        f"High or critical advisory rows by the first segment of resolution.path: {roots}.",
        "EV-AUDIT-FULL",
    )
    job_text = []
    for job in ci["jobs"]:
        skipped = [step["name"] for step in job["steps"] if step["conclusion"] == "skipped"]
        job_text.append(f"{job['name']} conclusion {job['conclusion']}; skipped steps: {', '.join(skipped)}")
    add(
        (
            f"CI run {ci['id']} {ci['html_url']} name {ci['name']} event {ci['event']} "
            f"head {ci['head_sha']} branch {ci['head_branch']} conclusion {ci['conclusion']}. "
            + ". ".join(job_text)
            + "."
        ),
        "EV-CI",
    )
    workflow = "; ".join(f"line {item['line']}: {item['text'].strip()}" for item in evidence["EV-WORKFLOW"]["lines"])
    add(f"Workflow {evidence['EV-WORKFLOW']['path']} at that commit: {workflow}.", "EV-WORKFLOW")
    add(
        (
            f"Open GitHub issues {issues['open_issues']} and open pull requests {issues['open_pull_requests']} "
            "on jondot/hygen. Rank is reactions.total_count, then comments, then issue number."
        ),
        "EV-ISSUES",
    )
    add(
        (
            f"Fork pull {pr['html_url']} state {pr['state']} draft {pr['draft']} merged {pr['merged']} "
            f"head {pr['head_ref']} {pr['head_sha']} base {pr['base_ref']} title {pr['title']}."
        ),
        "EV-PR",
    )
    logger = evidence["EV-SNIPPETS"]["logger"]
    chalk = evidence["EV-SNIPPETS"]["chalk"]
    metaverse = evidence["EV-SNIPPETS"]["metaverse"]
    add(
        (
            f"{logger['path']}:{logger['line']} `{logger['text']}`. "
            f"chalk {chalk['version']} has {chalk['templates_path']} (exists {chalk['exists']}). "
            f"{metaverse['path']}:{metaverse['line']} `{metaverse['text']}`."
        ),
        "EV-SNIPPETS",
    )
    commands = prov["commands"]
    add(
        (
            f"Collection commands ran on Node {prov['node']} with yarn {prov['yarn']}. "
            f"outdated sha256 {commands['outdated']['sha256']}; "
            f"full audit sha256 {commands['audit_full']['sha256']} ({commands['audit_full']['bytes']} bytes, stored reduced); "
            f"production audit sha256 {commands['audit_prod']['sha256']}. "
            f"GET /user HTTP {prov['github_user']['status']} {prov['github_user']['message']}. "
            f"windows job: {prov['windows_job']}."
        ),
        "EV-PROVENANCE",
    )
    return [{"id": f"F{index + 1}", **item} for index, item in enumerate(lines)]


def _decisions(snapshot: dict[str, Any], selection: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []

    def add(text: str, *ids: str) -> None:
        rows.append({"text": text, "evidence_ids": list(ids), "citations": _cite(snapshot, *ids)})

    for item in selection["skipped"]:
        issue = item["issue"]
        evidence_ids = ["EV-ISSUES", "EV-CI"] if "yarn build" in item["reason"] else ["EV-ISSUES"]
        add(
            (
                f"Skip #{issue['number']} {issue['html_url']} "
                f"(reactions {issue['reactions_total']}, comments {issue['comments']}): {item['reason']}."
            ),
            *evidence_ids,
        )
    nxt = selection["next_eligible"]
    if nxt:
        add(
            (
                f"#{nxt['number']} {nxt['html_url']} also has a fenced expected behavior "
                f"(reactions {nxt['reactions_total']}, comments {nxt['comments']}) and is the next issue after the three. "
                "It is not scheduled. The plan keeps three issue commits."
            ),
            "EV-ISSUES",
        )
    outdated = _outdated_index(snapshot)
    for name in ("ejs", "degit"):
        row = outdated[name]
        add(
            (
                f"Do not bump {name}: current {row['current']} equals wanted {row['wanted']}, "
                f"latest {row['latest']} is a different major, and {name} is not a module in the 20 production advisories."
            ),
            "EV-OUTDATED",
            "EV-AUDIT-PROD",
        )
    pkg = next(item for item in snapshot["evidence"]["EV-AUDIT-FULL"]["direct_advisories"] if item["module"] == "pkg")
    pkg_row = outdated["pkg"]
    if in_range(pkg_row["wanted"], pkg["vulnerable"]) is not True:
        raise ValueError("pkg wanted release is not inside the cited vulnerable range")
    add(
        (
            f"Do not bump pkg as a fix. {pkg['ghsa']} vulnerable {pkg['vulnerable']} patched {pkg['patched']}. "
            f"current {pkg_row['current']} wanted {pkg_row['wanted']} latest {pkg_row['latest']}. "
            "The patched value names no release, and the wanted release stays inside the vulnerable range."
        ),
        "EV-AUDIT-FULL",
        "EV-OUTDATED",
    )
    build = next(issue for issue in snapshot["evidence"]["EV-ISSUES"]["issues"] if issue["number"] == 378)
    add(
        (
            f"Do not schedule #{build['number']} as a remaining build failure. "
            f"Title {build['title']}. Both cited jobs concluded success on the step `Run yarn build`."
        ),
        "EV-ISSUES",
        "EV-CI",
    )
    return [{"id": f"D{index + 1}", **item} for index, item in enumerate(rows)]


def build_upgrade_prd(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("agent_branch") != AGENT_BRANCH:
        raise ValueError(f"agent branch {snapshot.get('agent_branch')} is not {AGENT_BRANCH}")
    selection = select_issues(snapshot)
    tasks = [
        _toolchain_task(snapshot),
        _resolution_task(snapshot),
        _gh_pages_task(snapshot),
        *[_issue_task(snapshot, issue) for issue in selection["commit_order"]],
        _windows_task(snapshot),
        _major_task(snapshot),
    ]
    for index, task in enumerate(tasks, start=1):
        task["id"] = f"U{index}"
        task["prompt"] = _prompt(snapshot, task)
    return {
        "repo_id": snapshot["repo_id"],
        "kind": "upgrade",
        "drafted_by": "deterministic",
        "llm_drafted": False,
        "commit": snapshot["commit"],
        "fork": snapshot["fork"],
        "agent_branch": snapshot["agent_branch"],
        "collected_at": snapshot["collected_at"],
        "base_ci_run": snapshot["evidence"]["EV-CI"]["html_url"],
        "problem": (
            "The minimal revival commit is the starting point. "
            "The remaining work is a refactor of the outdated TypeScript toolchain and the advisories that still have a patched release, "
            "a Windows run of the existing win32 test, and three small upstream issues. "
            "Each task is its own commit so the ubuntu CI can stay green between them."
        ),
        "findings": _findings(snapshot),
        "decisions": _decisions(snapshot, selection),
        "tasks": tasks,
        "out_of_scope": [
            "Do not redo the revival commit's Node 20/22 workflow, the ejs and degit floors, or the --version flag.",
            "Do not merge the fork pull request.",
            "Do not treat a hypothesis as an observed CI result.",
            "Do not add network calls to the hygen test suite.",
        ],
    }


def render_upgrade_markdown(document: dict[str, Any]) -> str:
    lines = [
        f"# Upgrade PRD: {document['repo_id']}",
        "",
        f"Drafted by: {document['drafted_by']}. llm_drafted: {str(document['llm_drafted']).lower()}.",
        "Kind: upgrade. This is the refactor and upgrade plan after the minimal revival.",
        "It does not replace docs/revival/jondot__hygen.prd.md.",
        f"Commit: {document['commit']}",
        f"Fork: {document['fork']}",
        f"Agent branch: {document['agent_branch']}",
        f"Base CI: {document['base_ci_run']}",
        f"Evidence collected at: {document['collected_at']}",
        "",
        "## Problem statement",
        "",
        "Non-factual framing. This section sets the task. It is not a cited evidence claim.",
        "",
        document["problem"],
        "",
        "## Evidence-backed findings",
        "",
    ]
    for finding in document["findings"]:
        lines.append(f"- {finding['id']}: {finding['text']}")
        lines.append(f"  - {_citation(finding)}")
    lines.extend(["", "## Planner decisions", ""])
    for decision in document["decisions"]:
        lines.append(f"- {decision['id']}: {decision['text']}")
        lines.append(f"  - {_citation(decision)}")
    lines.extend(
        [
            "",
            "## Tasks",
            "",
            "Commit order is the order below. Each task is one commit. The ubuntu jobs stay the gate between commits.",
            "",
        ]
    )
    for task in document["tasks"]:
        lines.extend(
            [
                f"### {task['id']} — {task['title']}",
                "",
                f"Change: {task['change']}",
                "",
                f"Acceptance: {task['acceptance']}",
                "",
                f"Risk: {task['risk']}",
                "",
                f"Hypothesis: {task['hypothesis']}",
                "",
                "Evidence:",
            ]
        )
        for citation in task["citations"]:
            lines.append(f"- {_citation(citation)}")
        lines.extend(["", "Prompt:", "", "```text", task["prompt"], "```", ""])
    lines.extend(
        [
            "## Out of scope",
            "",
            "Non-factual framing. These limits are instructions, not cited evidence claims.",
            "",
        ]
    )
    for item in document["out_of_scope"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_upgrade_json(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _citation(item: dict[str, Any]) -> str:
    if item.get("evidence_id") and item.get("content_hash"):
        return f"evidence: {item['evidence_id']}; content_hash: {item['content_hash']}"
    citations = item.get("citations") or []
    if citations and "evidence_id" in citations[0]:
        return "; ".join(_citation(citation) for citation in citations)
    evidence = ", ".join(item.get("evidence_ids") or [])
    return f"evidence: {evidence}"
