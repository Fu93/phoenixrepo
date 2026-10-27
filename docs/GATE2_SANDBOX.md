# Gate 2 Docker sandbox

Status: **experimental, opt-in, smoke-tested on a tiny fixture only.** No real CANDIDATE repository has been run
through it. Nothing here shows that Gate 2 works; it only shows that the container plumbing behaves as described
below on one Windows machine (Docker Desktop, Docker Engine 29.9.2, images `python:3.12-slim` and `node:22-slim`).

Gate 2 runs a candidate repository's own install and test commands. Those commands are untrusted code.
`--sandbox-mode docker` runs them in a container instead of on the host. The default is `--sandbox-mode host`
and is unchanged.

```
python scripts/revive.py ... --sandbox-mode docker     # needs a running Docker Engine
python scripts/gate2_sandbox_smoke.py                  # real-container smoke test on a fixture repo
python -m pytest tests/test_gate2_docker_sandbox.py    # offline tests (no Docker needed)
python -m pytest -m docker                             # real-Docker tests (skipped if `docker info` fails)
```

## What it does

- **One persistent container per run.** `docker run -d --name phoenix-g2-<run id> ... ` once, then one
  `docker exec` per command, so a `pip install` in one command is still there for the next one. The container is
  removed by `close()` in a `finally` (also after exceptions) and by an `atexit` safety net.
- **Image by project type.** Chosen once, from the clone's manifest, with the same precedence as `detect_commands`:
  `package.json` -> `node:22-slim`; `pyproject.toml`, `requirements.txt`, `setup.py` or `setup.cfg` ->
  `python:3.12-slim`; no manifest -> `python:3.12-slim`. An explicit `image=` argument overrides it.
  **Go (`go.mod`) is not supported**: every command returns exit 125 with "Go projects (go.mod) are not supported
  by the Docker sandbox ... Use --sandbox-mode host", nothing is started, and `scripts/revive.py` stops before any
  model call. A repository with several manifests is treated as its first match (a repo with a `package.json` used
  only for tooling runs in the Node image).
- **Writable state without a writable root.** The root filesystem is `--read-only`. `HOME`, `TMPDIR`, the pip and
  npm caches live on a tmpfs at `/sandbox` owned by uid 1000. For Python it also holds a venv (`/sandbox/venv`,
  first on `PATH`, so `pip` and `python -m pytest` use it). It lasts as long as the container and is never written
  into the clone. `/tmp` is a separate `noexec` tmpfs. For Node, `node_modules` is installed into the clone (the
  rw mount), as in host mode; a staged tree (see below) gets a symlink to it.
- **Only the clone is mounted.** The clone is bind-mounted read-write at `/workspace` and its `.git` is mounted
  read-only on top. No other host path, no `.env`, no `docker.sock`, no `--env-file`. The host environment is not
  passed in; only a fixed baseline plus caller variables whose names do not look like credentials.
  A directory outside the clone (the verifier re-runs checks in temporary copies) is copied into the container's
  tmpfs with `tar` as the non-root user, run there, and deleted.
- **Hardening flags.** `--user 1000:1000` (root is refused), `--cap-drop ALL`, `--security-opt no-new-privileges`,
  Docker's default seccomp profile (not overridden), `--pids-limit 512`, `--memory 2g` (swap off), `--cpus 2`,
  `--ulimit nofile=4096:4096 --ulimit core=0`, `--ipc private`, read-only root, no `--privileged`, never the host
  network (`network_name` of `host`, `none` or `container:*` is refused), a PID 1 that reaps orphans.
  The smoke test measures these from inside and with `docker inspect`: seccomp mode 2, `ulimit -n/-c`,
  `mount` and `unshare -Ur` denied, raw sockets denied, cgroup `memory.max`/`pids.max`/`cpu.max`, a fork loop
  stopped at 508 processes, and the container's HostConfig and mount list. Once the pids limit is hit even
  `docker exec` cannot start a process; the timeout path then removes and re-creates the container.
- **gVisor (runsc): not available here, not used, not claimed.** `docker info` on this Docker Desktop lists the
  runtimes `runc`, `io.containerd.runc.v2` and `nvidia`; there is no `runsc`. The smoke test reports
  `gvisor_runsc_available` as SKIP. `DockerSandboxRunner(runtime=...)` passes `--runtime` through when asked; it has
  never been exercised with a real alternative runtime.
- **Explicit network phases.** The caller passes `phase=`. Only `install` (the loop's `_install`, and the `npm
  install --no-save` repair in `ensure_env`) gets a network. Before each command the runner attaches the container
  to `bridge` (install) or runs `docker network disconnect` (every other phase, and any unknown or missing phase).
  The command text is never inspected. If the network state cannot be set, the command is not run.
  npm projects: `npm install`/`npm ci` run in the install phase with network; `npm test` runs offline.
  The loop's registry lookups are explicit install-phase calls (`_run_lookup`): `npm outdated`, `pip list --outdated`,
  the `npm view` / `pip index versions` version probes, and the quality gate's `npm audit` / `pip-audit` /
  freshness commands. They are fixed commands, they feed the same code as before, and no verdict logic changed.
  If a lookup could not run or could not reach the registry (exit 125, or a network error in its output), the
  sandboxed loop records a `REVIVE_LOOKUP_UNAVAILABLE` trace event ("dependency lookup unavailable in the sandbox;
  nothing was derived from it"). Host-mode traces do not get this event. Install-phase network access is also
  available to those lookups, which run in the same container as the clone.
- **Timeouts.** A timed-out command kills every process in the container except PID 1 (`kill -9 -1`) and the
  container, with its venv, carries on. If that fails (for example the pids limit blocks the exec) the container is
  removed and a fresh one with an empty venv is started for the next command.

## What it does NOT guarantee

- **It is not a proven security boundary.** It is a set of standard Docker hardening flags. A kernel, runtime or
  Docker Desktop VM escape defeats it, and nobody has audited it.
- **Docker Desktop and its VM are trusted.** What still depends on them:
  the Linux kernel in the Docker Desktop VM, which every container shares (a kernel bug defeats seccomp, caps and
  namespaces); runc and containerd; the Docker daemon and whoever can talk to it; the file-sharing layer that
  implements the bind mount of the clone (it decides ownership, permissions and what the read-only `.git` mount
  really enforces); the images, pulled by tag (`python:3.12-slim`, `node:22-slim`) and not pinned by digest, from
  Docker Hub; and the host-side Python that builds the commands. Using `image=` with a digest pins an image.
- **The clone is writable.** Candidate code can modify, delete or plant anything in the working tree (only `.git` is
  read-only). The loop snapshots and restores the tree, but that is a check, not a barrier.
  If the candidate repository itself contains a `.env`, it is visible inside the container, because it is part of
  the clone. Nothing outside the clone is.
- **Acceptance commands still run inside the container.** The model writes the work items' `acceptance_command`
  and the synthesized tests; they run with the same privileges as the repo's own tests. During an `install` phase
  that includes network access, so install-time code can send anything it can read (the clone and its history)
  to the internet. Test and verify phases have no network.
- **Resource limits are limits, not isolation of cost**: a command can still use all of its CPU, memory, tmpfs and
  disk writes in the mount until it times out.
- **tmpfs counts against memory.** `/sandbox` (1g) and `/tmp` (256m) are memory-backed, so a large dependency set
  can fail to install; they are configurable on `DockerSandboxRunner`.
- **Windows bind mounts.** On Docker Desktop (WSL2) the NTFS bind mount was writable for uid 1000 and the nested
  read-only `.git` mount held, but that depends on the Docker Desktop version and the drive's sharing settings.
  If the mount is not writable the smoke test reports `workspace_writable_by_non_root` as FAIL and no automatic
  workaround is applied; run from a Linux/WSL path or fix the ACL.
- **Smoke-tested on fixtures only.** One pure-python repo (one passing, one failing test) and one npm repo (one
  dependency, one passing test). Real candidates (native extensions, services, large dependency trees, lifecycle
  scripts that need more than the container offers) are untested.
- **No Gate 2 claim.** This changes where commands run. It does not change what counts as a revival, and it does not
  make a Gate 2 verdict true.

## verification.json seal (applies to host mode too)

`write_reports()` writes `verification.json` and records its SHA-256 as one `GATE2_VERIFICATION_SEAL` trace event
(agent `validator`, fields `sha256`, `size_bytes`, `checks_count`). It is the single authoritative seal: it runs
after the final verification is determined, `revive()` itself no longer seals, the hash is taken from the bytes on
disk, and sealing different content into the same trace raises `VerificationSealConflict`. The file is written as
bytes (LF), so the hash is the same on every OS.

`check_verification_seal(verification.json, trace.jsonl)` compares the file with the event, so an edit to
`verification.json` after the run is detectable. `scripts/verify_trace.py` still validates the hash chain over a
trace that contains the event. Neither detects a trace that was rewritten and re-chained as a whole; that needs the
recorded run hash.

Host-mode runs (the default) gain **one extra trace event** when run through `scripts/revive.py`. Verdicts, states
and `revive()` behaviour are unchanged: `revive()` called directly adds no event, and `_revive_impl` keeps
`runner=shell_runner` as its default.

## Files

- `app/revive_sandbox.py` - `CommandRunner`, `HostRunner`, `DockerSandboxRunner`, `seal_verification`, `check_verification_seal`
- `app/revive_loop.py` - explicit `phase=` on every runner call, `sandbox_mode` on `revive()`
- `scripts/gate2_sandbox_smoke.py` - real-container smoke test
- `tests/test_gate2_docker_sandbox.py` (offline), `tests/test_gate2_docker_smoke.py` (`@pytest.mark.docker`)