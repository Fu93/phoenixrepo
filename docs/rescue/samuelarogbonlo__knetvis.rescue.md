# Rescue plan: samuelarogbonlo/knetvis

Gate 1: **CANDIDATE**. Drafted by: Nemotron (served `nvidia/nemotron-3-ultra-550b-a55b`), verified by code.
Trace: `logs/trace-rescue-samuelarogbonlo__knetvis-20261027T204407Z.jsonl`, run hash `c36efde5526a2c082912e628e22d3babb6e5a0e96521d767794cb34c34aa9016`.

Every item below cites evidence that gate 1 recorded, and its quote was found verbatim in that evidence. Items that failed the check are listed at the end and are not part of the plan.

## Positioning

- **User:** Kubernetes platform engineers and security teams who manage Network Policies
- **Job:** visualize and test Kubernetes Network Policies from the CLI
- **Gap to own:** maintained free OSS alternatives lack a CLI that both visualizes policies and tests connectivity between pods; browser tools (artturik, calico-visualiser) are unmaintained; illuminatio is archived; kubectl-reach only tests connectivity from a live pod, not policy validation
- Evidence: `candidate:samuelarogbonlo/knetvis`, `assessment:differentiation`, `must_beat:artturik/network-policy-viewer`, `must_beat:inovex/illuminatio`, `must_beat:amdadulbari/kubectl-reach`

## Can gate 2 verify it?

**verifiable.** A test path is visible in the root, so gate 2's execution loop can verify the REVIVE stage.

## Stage 1 — REVIVE

### R1. Restore CLI visualize and test commands against test-policy.yaml

- **Why:** The candidate claims to be a CLI tool for visualizing and testing Network Policies; the repo includes a test policy and test directory but the README is truncated, so the round-trip must be verified.
- **Acceptance:** Running `knetvis` against `test-policy.yaml` produces a visualization output and test results without errors.
- **Evidence:** `candidate:samuelarogbonlo/knetvis`, `repo:tree` — “A powerful CLI tool for visualizing and testing Kubernetes Network Policies.”

## Stage 2 — OPTIMISE

### O1. Pin and update Python dependencies; ensure CI passes on Python 3.8+

- **Why:** The project declares Python 3.8+ support and provides requirements files; dependency drift can break the revive.
- **Acceptance:** CI workflow passes on Python 3.8, 3.9, 3.10, 3.11, 3.12 with pinned dependencies in requirements.txt and requirements-dev.txt.
- **Evidence:** `candidate:samuelarogbonlo/knetvis`, `repo:tree` — “Python 3.8+”

### O2. Expand test coverage for visualize and test code paths

- **Why:** The repo has a tests/ directory and test-policy.yaml; coverage ensures the revive stays verifiable.
- **Acceptance:** pytest coverage >= 80% for src/ modules; new tests exercise cross-namespace policy visualization.
- **Evidence:** `repo:tree`, `assessment:feasibility` — “Structured codebase with 6 non-boilerplate source paths (ci-check.sh, images/Real-demo.gif, setup.py). Solid working baseline.”

## Stage 3 — UPGRADE

### U1. Implement cross-namespace connection visualization

- **Why:** Differentiation identifies 'Show cross-namespace connections' as a capability not covered by any maintained free OSS alternative; must_beat alternatives (artturik, gerald1248) are unmaintained browser tools.
- **Acceptance:** Given a multi-namespace NetworkPolicy fixture, `knetvis visualize` renders namespace-scoped edges and labels them in the output.
- **Evidence:** `assessment:differentiation`, `must_beat:artturik/network-policy-viewer`, `must_beat:gerald1248/k8s-network-policy-viewer` — “Missing capabilities: 'Visualize network policies', 'Test connectivity between pods', 'Validate network policy files', 'Show cross-namespace connections', 'Generate visualizations of network policies' (5 not covered by any maintained free OSS alternative).”

### U2. Add network policy file validation (lint) command

- **Why:** Differentiation lists 'Validate network policy files' as a missing capability in maintained alternatives; the candidate can own this gap.
- **Acceptance:** New `knetvis validate` command exits non-zero and reports errors for invalid NetworkPolicy YAML (missing required fields, bad selectors).
- **Evidence:** `assessment:differentiation`, `must_beat:inovex/illuminatio` — “Missing capabilities: 'Visualize network policies', 'Test connectivity between pods', 'Validate network policy files', 'Show cross-namespace connections', 'Generate visualizations of network policies' (5 not covered by any maintained free OSS alternative).”

### U3. Add pod-to-pod connectivity test simulation (offline)

- **Why:** Differentiation lists 'Test connectivity between pods' as uncovered; kubectl-reach only tests from a live pod via ephemeral container, not offline policy simulation.
- **Acceptance:** New `knetvis test` command takes a policy set and pod labels, outputs allowed/denied matrix without a running cluster.
- **Evidence:** `assessment:differentiation`, `must_beat:amdadulbari/kubectl-reach` — “tests network connectivity from an existing Pod to a target (IP, DNS, or Service) by injecting an ephemeral debug container”

## Must beat

- **artturik/network-policy-viewer**: Provide a maintained CLI that visualizes policies and tests connectivity offline, whereas the alternative is an unmaintained browser tool (last update 2024-11-28). (`must_beat:artturik/network-policy-viewer`)
- **sbulav/calico-policy-visualiser**: Deliver a CLI-first, zero-dependency visualization that works for vanilla Kubernetes NetworkPolicies, not just Calico, and is actively maintained. (`must_beat:web:https://github.com/sbulav/calico-policy-visualiser`)
- **inovex/illuminatio**: Offer active maintenance and a simpler CLI workflow (no cluster scan required for validation), while illuminatio is archived. (`must_beat:inovex/illuminatio`)
- **gerald1248/k8s-network-policy-viewer**: Provide a maintained CLI with cross-namespace visualization; the alternative is unmaintained since 2023 and only outputs JSON/YAML/dot. (`must_beat:gerald1248/k8s-network-policy-viewer`)
- **amdadulbari/kubectl-reach**: Enable offline policy simulation and validation, not just live connectivity tests via ephemeral containers. (`must_beat:amdadulbari/kubectl-reach`)

## Risks

- No external demand signals (demand_evidence score 0) — adoption may stay low.
- Zero moat (moat score 0) — any contributor can replicate the CLI approach.
- Candidate README is truncated; actual feature set unknown — revive may uncover missing core functionality.
- All must_beat alternatives are unmaintained or partial overlap; market may not perceive a strong need for another tool.

## Evidence

| id | kind | source | content hash |
| --- | --- | --- | --- |
| `candidate:samuelarogbonlo/knetvis` | candidate | https://github.com/samuelarogbonlo/knetvis | — |
| `prior:web:https://github.com/artturik/network-policy-viewer` | prior | https://github.com/artturik/network-policy-viewer | — |
| `prior:web:https://kubernetes.io/blog/2017/10/enforcing-network-policies-in-kubernetes` | prior | https://kubernetes.io/blog/2017/10/enforcing-network-policies-in-kubernetes | — |
| `prior:web:https://github.com/sbulav/calico-policy-visualiser` | prior | https://github.com/sbulav/calico-policy-visualiser | — |
| `prior:web:https://k8studio.io/blogs/best-kubernetes-cluster-visualization-tools-2026/` | prior | https://k8studio.io/blogs/best-kubernetes-cluster-visualization-tools-2026/ | — |
| `prior:artturik/network-policy-viewer` | prior | https://github.com/artturik/network-policy-viewer | — |
| `prior:amdadulbari/kubectl-reach` | prior | https://github.com/amdadulbari/kubectl-reach | — |
| `prior:inovex/illuminatio` | prior | https://github.com/inovex/illuminatio | — |
| `prior:gerald1248/k8s-network-policy-viewer` | prior | https://github.com/gerald1248/k8s-network-policy-viewer | — |
| `must_beat:web:https://github.com/artturik/network-policy-viewer` | must_beat | — | — |
| `must_beat:web:https://github.com/sbulav/calico-policy-visualiser` | must_beat | — | — |
| `must_beat:web:https://k8studio.io/blogs/best-kubernetes-cluster-visualization-tools-2026/` | must_beat | — | — |
| `must_beat:artturik/network-policy-viewer` | must_beat | — | — |
| `must_beat:inovex/illuminatio` | must_beat | — | — |
| `must_beat:gerald1248/k8s-network-policy-viewer` | must_beat | — | — |
| `must_beat:amdadulbari/kubectl-reach` | must_beat | — | — |
| `must_beat:web:https://kubernetes.io/blog/2017/10/enforcing-network-policies-in-kubernetes` | must_beat | — | — |
| `assessment:problem_severity` | assessment | — | — |
| `assessment:demand_evidence` | assessment | — | — |
| `assessment:differentiation` | assessment | — | — |
| `assessment:feasibility` | assessment | — | — |
| `assessment:productization_path` | assessment | — | — |
| `assessment:moat` | assessment | — | — |
| `repo:tree` | repo | https://github.com/samuelarogbonlo/knetvis | — |
