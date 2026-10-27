"""Real Docker smoke test for the Gate 2 sandbox. Skipped when Docker is not available.

Runs scripts/gate2_sandbox_smoke.py: a throw-away fixture repo through a real container. Network
dependent checks are reported as skipped (not passed) when the machine itself is offline.
"""

from __future__ import annotations

import pytest

from scripts.gate2_sandbox_smoke import docker_available, run_smoke

pytestmark = pytest.mark.docker

# Everything that does not need the internet must pass whenever Docker works.
OFFLINE_CHECKS = {
    "container_starts_and_venv_on_path",
    "non_root_user",
    "git_dir_read_only",
    "workspace_writable_by_non_root",
    "root_filesystem_read_only",
    "no_env_file_visible",
    "no_host_env_secrets",
    "no_capabilities_no_new_privs_no_docker_sock",
    "network_unreachable_after_disconnect",
    "timeout_kills_process_tree_and_container_survives",
    "cwd_outside_clone_is_staged",
    "container_removed_afterwards",
}


@pytest.fixture(scope="module")
def smoke():
    ok, info = docker_available()
    if not ok:
        pytest.skip(f"Docker is not available: {info}")
    return {check.name: check for check in run_smoke()}


def test_no_smoke_check_failed(smoke):
    failed = {name: check.detail for name, check in smoke.items() if check.status == "FAIL"}
    assert not failed, failed


def test_offline_isolation_checks_all_pass(smoke):
    assert OFFLINE_CHECKS <= set(smoke)
    assert {name: smoke[name].status for name in OFFLINE_CHECKS} == {name: "PASS" for name in OFFLINE_CHECKS}


def test_install_then_test_flow_works_when_the_machine_is_online(smoke):
    flow = ("network_available_in_install_phase", "pip_install_into_venv_in_install_phase", "installed_package_importable_in_test_phase", "pytest_in_sandbox_no_network_expected_results")
    if smoke["network_available_in_install_phase"].status == "SKIP":
        pytest.skip(smoke["network_available_in_install_phase"].detail)
    assert {name: smoke[name].status for name in flow} == {name: "PASS" for name in flow}