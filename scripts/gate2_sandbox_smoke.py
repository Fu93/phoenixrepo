"""Real-Docker smoke test for the Gate 2 sandbox (DockerSandboxRunner). Needs a running Docker Engine.

Runs tiny throw-away fixture repos through the real runner and asserts the isolation properties from
INSIDE the container: a Python repo (one passing and one failing pytest test), an npm repo (one
dependency, one passing test; install with network, test offline) and a Go repo (must be refused). It never calls a model and
never reads a .env or a secret. It has only ever been run on this fixture, not on a real candidate.

    python scripts/gate2_sandbox_smoke.py [--image python:3.12-slim] [--node-image node:22-slim] [--json]

Exit code: 0 = no check failed (network checks may be SKIP when the machine is offline),
1 = a check failed, 2 = Docker is not available.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.revive_sandbox import DEFAULT_IMAGE, NODE_IMAGE, SANDBOX_LABEL, UNSUPPORTED_GO_MESSAGE, DockerSandboxRunner  # noqa: E402

PYPI_URL = "https://pypi.org/simple/six/"
TINY_PACKAGE = "six"  # one pure-python file
PROBE_URL_CODE = (
    "import sys,urllib.request;"
    f"r=urllib.request.urlopen('{PYPI_URL}',timeout=15);print('HTTP',r.status)"
)
BLOCKED_CODE = (
    "import socket,urllib.request\n"
    "bad=[]\n"
    "for label,fn in (('connect_ip',lambda:socket.create_connection(('1.1.1.1',443),4)),"
    "('dns',lambda:socket.getaddrinfo('pypi.org',443)),"
    f"('https',lambda:urllib.request.urlopen('{PYPI_URL}',timeout=5))):\n"
    "    try:\n"
    "        fn(); bad.append(label)\n"
    "    except Exception:\n"
    "        pass\n"
    "print('REACHABLE:'+','.join(bad) if bad else 'BLOCKED')\n"
)


@dataclass
class Check:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


def docker_available(docker_bin: str = "docker") -> tuple[bool, str]:
    try:
        done = subprocess.run([docker_bin, "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if done.returncode != 0:
        return False, (done.stderr or done.stdout).decode("utf-8", "replace").strip()[-300:]
    return True, done.stdout.decode("utf-8", "replace").strip()


def host_has_network() -> bool:
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=15) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)


def make_fixture(parent: Path) -> Path:
    """A tiny pure-python repo: calc.py, one passing and one failing test, and a real .git directory."""
    repo = parent / "fixture_repo"
    repo.mkdir(parents=True)
    (repo / "requirements.txt").write_text("# no dependencies; pytest is installed by the smoke test\n", encoding="utf-8")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\ndef test_broken():\n    assert add(1, 1) == 3\n",
        encoding="utf-8",
    )
    try:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return repo


def run_smoke(image: str = DEFAULT_IMAGE, node_image: str = NODE_IMAGE) -> list[Check]:
    checks: list[Check] = []

    def add(name: str, ok: bool | None, detail: str = "") -> bool:
        checks.append(Check(name, "SKIP" if ok is None else ("PASS" if ok else "FAIL"), detail.strip()[:600]))
        return bool(ok)

    # Dummy host secrets: they must never show up inside the container.
    os.environ["FAKE_API_KEY"] = "dummy"
    os.environ["FAKE_HOST_VALUE"] = "dummy"

    have = _docker("image", "inspect", image)
    if have.returncode != 0:
        pulled = _docker("pull", image, timeout=900)
        if not add("image_available", pulled.returncode == 0, f"docker pull {image}: exit {pulled.returncode} {pulled.stderr[-200:]}"):
            return checks
    else:
        add("image_available", True, image)

    online = host_has_network()
    run_id = f"smoke-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix="g2smoke-") as tmp:
        parent = Path(tmp)
        (parent / ".env").write_text("HOST_PARENT_SECRET=must-not-be-visible\n", encoding="utf-8")
        repo = make_fixture(parent)
        runner = DockerSandboxRunner(run_id=run_id)  # image chosen from the fixture's manifest
        try:
            def sh(cmd: str, phase: str = "test", timeout: int = 120, cwd: Path | None = None):
                return runner(cmd, cwd or repo, timeout, phase=phase)

            boot = sh("which python; python --version")
            add("python_image_auto_selected", runner.kind == "python" and runner.image_used == image, f"kind={runner.kind} image={runner.image_used}")
            add("container_starts_and_venv_on_path", boot.ok and "/sandbox/venv/bin/python" in boot.output, boot.output)
            if not boot.ok:
                return checks

            uid = sh("id -u")
            add("non_root_user", uid.ok and uid.output.strip() not in ("", "0"), f"id -u -> {uid.output.strip()}")

            probe = sh("echo x > /workspace/.git/g2_probe")
            add("git_dir_read_only", (not probe.ok) and not (repo / ".git" / "g2_probe").exists(), f"exit {probe.code}: {probe.output.strip()[-160:]}")

            wr = sh("echo ok > /workspace/g2_write_probe")
            host_saw = (repo / "g2_write_probe").exists()
            add(
                "workspace_writable_by_non_root",
                wr.ok and host_saw,
                f"exit {wr.code}; visible on host: {host_saw}. " + ("" if wr.ok else "Windows bind mount is not writable for uid 1000: run the sandbox on a Linux/WSL path or loosen the mount ACL; no automatic workaround is applied. ") + wr.output.strip()[-160:],
            )
            (repo / "g2_write_probe").unlink(missing_ok=True)

            ro = sh("touch /usr/g2_probe")
            add("root_filesystem_read_only", not ro.ok, f"exit {ro.code}: {ro.output.strip()[-120:]}")

            envf = sh("ls -a /workspace; echo ---; find / -name .env -not -path '/proc/*' 2>/dev/null; echo END")
            listing, _, found = envf.output.partition("---")
            add("no_env_file_visible", envf.ok and ".env" not in listing.split() and found.replace("END", "").strip() == "", envf.output)

            env = sh("env")
            add("no_host_env_secrets", env.ok and "FAKE_API_KEY" not in env.output and "FAKE_HOST_VALUE" not in env.output and "dummy" not in env.output, "FAKE_API_KEY/FAKE_HOST_VALUE set on host; container env has " + str(len([x for x in env.output.splitlines() if x])) + " vars")

            priv = sh("grep -E '^(CapEff|NoNewPrivs):' /proc/self/status; test ! -e /var/run/docker.sock && echo NO_DOCKER_SOCK")
            add("no_capabilities_no_new_privs_no_docker_sock", priv.ok and "CapEff:\t0000000000000000" in priv.output and "NoNewPrivs:\t1" in priv.output and "NO_DOCKER_SOCK" in priv.output, priv.output)

            # --- install phase: network on, pip into the venv ---------------------------------
            net_on = sh(f'python -c "{PROBE_URL_CODE}"', phase="install", timeout=60)
            if net_on.ok:
                add("network_available_in_install_phase", True, net_on.output)
            elif not online:
                add("network_available_in_install_phase", None, "SKIP: this machine has no network (host could not reach pypi.org either)")
            else:
                add("network_available_in_install_phase", False, f"host reaches pypi.org but the container did not: {net_on.output}")

            installed = False
            if net_on.ok:
                inst = sh(f"python -m pip install -q {TINY_PACKAGE} pytest && python -c \"import sys;print(sys.prefix)\"", phase="install", timeout=600)
                installed = inst.ok and "/sandbox/venv" in inst.output
                add("pip_install_into_venv_in_install_phase", installed, inst.output[-300:])
            else:
                add("pip_install_into_venv_in_install_phase", None, "SKIP: needs network")

            if installed:
                lk = sh("python -m pip list --outdated --format json", phase="install", timeout=120)
                try:
                    parsed = json.loads(lk.output[lk.output.index("["):lk.output.rindex("]") + 1])
                except ValueError:
                    parsed = None
                add("pip_outdated_lookup_works_in_install_phase", lk.ok and isinstance(parsed, list), f"exit {lk.code}; {lk.output.strip()[:160]}")
            else:
                add("pip_outdated_lookup_works_in_install_phase", None, "SKIP: needs network")

            # --- test phase: network cut ---------------------------------------------------
            if installed:
                imp = sh(f'python -c "import {TINY_PACKAGE};print({TINY_PACKAGE}.__version__)"')
                add("installed_package_importable_in_test_phase", imp.ok, imp.output)
            else:
                add("installed_package_importable_in_test_phase", None, "SKIP: install did not run")

            blocked = sh(f"python -c \"{BLOCKED_CODE}\"")
            nets = _docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", runner.name)
            add("network_unreachable_after_disconnect", blocked.ok and blocked.output.strip() == "BLOCKED" and nets.stdout.strip() == "{}", f"probe: {blocked.output.strip()[:200]}; attached networks: {nets.stdout.strip()}")

            if installed:
                t = sh("python -m pytest -q -p no:cacheprovider")
                good = t.code == 1 and "1 failed, 1 passed" in t.output and "test_broken" in t.output
                add("pytest_in_sandbox_no_network_expected_results", good, f"exit {t.code}: " + t.output.strip().splitlines()[-1] if t.output.strip() else f"exit {t.code}")
            else:
                add("pytest_in_sandbox_no_network_expected_results", None, "SKIP: pytest could not be installed (no network)")

            # --- timeout teardown ------------------------------------------------------------
            started = time.monotonic()
            slow = sh("sleep 300 & sleep 300", timeout=3)
            took = time.monotonic() - started
            # Look for the 300-second sleeps by command line (zombies have none), and give PID 1 a moment to reap.
            left = sh(
                "for i in 1 2 3 4 5 6 7 8 9 10; do n=$(grep -al 'slee[p].300' /proc/[0-9]*/cmdline 2>/dev/null | wc -l); "
                "[ $n = 0 ] && break; sleep 0.5; done; echo sleepers=$n"
            )
            alive = sh("python -c \"print('alive')\"")
            venv_kept = sh(f"python -c \"import {TINY_PACKAGE}\"") if installed else None
            add(
                "timeout_kills_process_tree_and_container_survives",
                slow.code == 124 and took < 60 and "sleepers=0" in left.output and alive.ok and (venv_kept is None or venv_kept.ok),
                f"exit {slow.code} after {took:.1f}s; {left.output.strip()}; venv kept: {None if venv_kept is None else venv_kept.ok}",
            )

            # --- a directory outside the clone (what the verifier does) ----------------------
            other = parent / "other_tree"
            other.mkdir()
            (other / "hello.py").write_text("print('hello from staged tree')\n", encoding="utf-8")
            st = sh("python hello.py", cwd=other)
            add("cwd_outside_clone_is_staged", st.ok and "hello from staged tree" in st.output, st.output)
            name = runner.name
        finally:
            runner.close()

        left_by_name = _docker("ps", "-a", "-q", "--filter", f"name={runner.name}")
        left_by_label = _docker("ps", "-a", "-q", "--filter", f"label={SANDBOX_LABEL}=1")
        add("container_removed_afterwards", runner.close_ok is True and left_by_name.stdout.strip() == "" and runner.name == name, f"close_ok={runner.close_ok}; docker ps -a by name: '{left_by_name.stdout.strip()}'; by label (any run): '{left_by_label.stdout.strip()}'")
    run_node_smoke(add, node_image, online)
    run_go_smoke(add)
    return checks


NODE_PROBE = """const net=require('net'),dns=require('dns');
const bad=[];let pending=2,over=false;
const done=()=>{if(--pending===0)console.log(bad.length?'REACHABLE:'+bad.join(','):'BLOCKED')};
dns.lookup('registry.npmjs.org',(e)=>{if(!e)bad.push('dns');done()});
const s=net.connect({host:'1.1.1.1',port:443});
const end=(ok)=>{if(over)return;over=true;if(ok)bad.push('ip');s.destroy();done()};
s.on('connect',()=>end(true));s.on('error',()=>end(false));s.setTimeout(4000,()=>end(false));
"""


def make_node_fixture(parent: Path) -> Path:
    """A tiny npm repo: one dependency (ms), one passing test, a real .git directory."""
    repo = parent / "node_fixture"
    repo.mkdir(parents=True)
    (repo / "package.json").write_text(
        json.dumps({"name": "g2-node-fixture", "version": "1.0.0", "private": True, "scripts": {"test": "node test.js"}, "dependencies": {"ms": "2.1.3"}}),
        encoding="utf-8",
    )
    (repo / "test.js").write_text(
        "const ms = require('ms');\nif (ms('2 days') !== 172800000) { console.error('wrong value'); process.exit(1); }\nconsole.log('node-fixture-ok');\n",
        encoding="utf-8",
    )
    (repo / "probe_net.js").write_text(NODE_PROBE, encoding="utf-8")
    try:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return repo


def run_node_smoke(add, node_image: str, online: bool) -> None:
    have = _docker("image", "inspect", node_image)
    if have.returncode != 0:
        pulled = _docker("pull", node_image, timeout=900)
        if not add("node_image_available", pulled.returncode == 0, f"docker pull {node_image}: exit {pulled.returncode} {pulled.stderr[-200:]}"):
            return
    with tempfile.TemporaryDirectory(prefix="g2smoke-node-") as tmp:
        repo = make_node_fixture(Path(tmp))
        runner = DockerSandboxRunner(run_id=f"smoke-node-{uuid.uuid4().hex[:8]}")
        try:
            def sh(cmd: str, phase: str = "test", timeout: int = 180):
                return runner(cmd, repo, timeout, phase=phase)

            who = sh("id -u; node -v; npm -v")
            add("node_image_auto_selected_non_root", runner.kind == "node" and runner.image_used == node_image and who.ok and who.output.split()[0] == "1000", f"kind={runner.kind} image={runner.image_used}; {who.output.strip()!r}")
            ro = sh("echo x > /workspace/.git/g2_probe")
            add("node_git_dir_read_only", not ro.ok and not (repo / ".git" / "g2_probe").exists(), f"exit {ro.code}")

            if online:
                inst = sh("npm install --no-audit --no-fund --loglevel=error", phase="install", timeout=600)
                add("npm_install_in_install_phase", inst.ok and (repo / "node_modules" / "ms").exists(), f"exit {inst.code}; node_modules/ms on host: {(repo / 'node_modules' / 'ms').exists()}; {inst.output.strip()[-300:]}")
            else:
                inst = None
                add("npm_install_in_install_phase", None, "SKIP: this machine has no network")

            blocked = sh("node probe_net.js", timeout=60)
            nets = _docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", runner.name)
            add("node_network_unreachable_in_test_phase", blocked.ok and blocked.output.strip() == "BLOCKED" and nets.stdout.strip() == "{}", f"probe: {blocked.output.strip()[:200]}; attached networks: {nets.stdout.strip()}")

            if online:
                lk = sh("npm view ms@2.1.3 version", phase="install", timeout=120)
                add("npm_version_probe_works_in_install_phase", lk.ok and "2.1.3" in lk.output, f"exit {lk.code}: {lk.output.strip()[-160:]}")
                # a package that was never queried while online, so the npm cache cannot answer
                off = sh("npm view is-number@7.0.0 version --fetch-retries=0 --fetch-timeout=10000", phase="test", timeout=90)
                add("npm_version_probe_unavailable_without_network", not off.ok, f"exit {off.code}: {off.output.strip()[-160:]}")
            else:
                add("npm_version_probe_works_in_install_phase", None, "SKIP: this machine has no network")
                add("npm_version_probe_unavailable_without_network", None, "SKIP: this machine has no network")
            if inst is not None and inst.ok:
                t = sh("npm test --silent")
                add("npm_test_offline_passes", t.ok and "node-fixture-ok" in t.output, f"exit {t.code}: {t.output.strip()[-200:]}")
                other = Path(tmp) / "node_stage"
                other.mkdir()
                (other / "t.js").write_text("console.log(require('ms')('1s'))\n", encoding="utf-8")
                st = runner("node t.js", other, 60, phase="verify")
                add("node_staged_tree_sees_installed_modules", st.ok and st.output.strip() == "1000", f"exit {st.code}: {st.output.strip()[-200:]}")
            else:
                add("npm_test_offline_passes", None, "SKIP: npm install did not run")
                add("node_staged_tree_sees_installed_modules", None, "SKIP: npm install did not run")
        finally:
            runner.close()
        left = _docker("ps", "-a", "-q", "--filter", f"name={runner.name}")
        add("node_container_removed_afterwards", runner.close_ok is True and left.stdout.strip() == "", f"close_ok={runner.close_ok}; docker ps -a by name: '{left.stdout.strip()}'")


def run_go_smoke(add) -> None:
    with tempfile.TemporaryDirectory(prefix="g2smoke-go-") as tmp:
        repo = Path(tmp) / "go_fixture"
        repo.mkdir()
        (repo / "go.mod").write_text("module example.com/g2\n\ngo 1.22\n", encoding="utf-8")
        runner = DockerSandboxRunner(run_id=f"smoke-go-{uuid.uuid4().hex[:8]}")
        try:
            res = runner("go test ./...", repo, 30, phase="test")
        finally:
            runner.close()
        left = _docker("ps", "-a", "-q", "--filter", f"name={runner.name}")
        add("go_project_refused_with_clear_message", runner.kind == "go" and res.code == 125 and UNSUPPORTED_GO_MESSAGE in res.output and left.stdout.strip() == "", f"kind={runner.kind} exit {res.code}: {res.output.strip()[:160]}; containers: '{left.stdout.strip()}'")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--node-image", default=NODE_IMAGE, dest="node_image")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    ok, info = docker_available()
    if not ok:
        print(f"Docker is not available: {info}")
        return 2
    results = run_smoke(args.image, args.node_image)
    if args.as_json:
        print(json.dumps([asdict(c) for c in results], indent=2))
    else:
        for c in results:
            print(f"[{c.status}] {c.name}: {c.detail.splitlines()[0] if c.detail else ''}")
        print(f"{sum(c.status == 'PASS' for c in results)} passed, {sum(c.status == 'FAIL' for c in results)} failed, {sum(c.status == 'SKIP' for c in results)} skipped")
    return 1 if any(c.status == "FAIL" for c in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())