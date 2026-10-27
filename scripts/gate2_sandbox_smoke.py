"""Real-Docker smoke test for the Gate 2 sandbox (DockerSandboxRunner). Needs a running Docker Engine.

Runs a tiny throw-away fixture repo (one passing and one failing pytest test) through the real
runner and asserts the isolation properties from INSIDE the container. It never calls a model and
never reads a .env or a secret. It has only ever been run on this fixture, not on a real candidate.

    python scripts/gate2_sandbox_smoke.py [--image python:3.12-slim] [--json]

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

from app.revive_sandbox import DEFAULT_IMAGE, SANDBOX_LABEL, DockerSandboxRunner  # noqa: E402

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


def run_smoke(image: str = DEFAULT_IMAGE) -> list[Check]:
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
        runner = DockerSandboxRunner(image=image, run_id=run_id)
        try:
            def sh(cmd: str, phase: str = "test", timeout: int = 120, cwd: Path | None = None):
                return runner(cmd, cwd or repo, timeout, phase=phase)

            boot = sh("which python; python --version")
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
            # A killed process stays a zombie until PID 1 reaps it, so count only live ones and give the reaper a moment.
            left = sh(
                "for i in 1 2 3 4 5 6 7 8 9 10; do n=0; for p in /proc/[0-9]*; do "
                "[ \"$(cat $p/comm 2>/dev/null)\" = sleep ] && ! grep -q '^State:.*Z' $p/status 2>/dev/null && n=$((n+1)); done; "
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
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    ok, info = docker_available()
    if not ok:
        print(f"Docker is not available: {info}")
        return 2
    results = run_smoke(args.image)
    if args.as_json:
        print(json.dumps([asdict(c) for c in results], indent=2))
    else:
        for c in results:
            print(f"[{c.status}] {c.name}: {c.detail.splitlines()[0] if c.detail else ''}")
        print(f"{sum(c.status == 'PASS' for c in results)} passed, {sum(c.status == 'FAIL' for c in results)} failed, {sum(c.status == 'SKIP' for c in results)} skipped")
    return 1 if any(c.status == "FAIL" for c in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())