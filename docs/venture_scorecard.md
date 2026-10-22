# Venture Scorecard

The venture scorecard is a deterministic review procedure for prioritizing abandoned GitHub idea repositories. Once candidate repositories have been filtered for idea quality and functional equivalence (`ALREADY_EXISTS` and `NO_GO` verdicts via `app/agents/market_filter.py` and `app/agents/equivalence.py`), the venture scorecard decides which remaining `CANDIDATE` is worth reviving into a viable open-source product.

## Non-Negotiable Invariants

1. **Never use the candidate repo's own stars or forks**: Stalled idea repositories typically have ~0 stars. The repository's own stars, forks, watchers, or issues are internal artifacts and must never be treated as quality, demand, or validation signals. Demand is measured strictly outside the candidate repository.
2. **Deterministic guard ("No evidence id, no points")**: Every score above 0 requires at least one verified evidence ID present in the supplied evidence set. If no valid evidence ID is cited, the score is deterministically clamped to 0 and flagged `UNSUPPORTED`. The LLM never has the last word.
3. **Ineligibility of rejected repositories**: Repositories with verdict `ALREADY_EXISTS` or `NO_GO` are strictly ineligible for revival prioritization.
4. **Feasibility threshold for protagonist selection**: To be selected as the revival protagonist, an eligible `CANDIDATE` must score at least `feasibility >= 2`.
5. **Moat is not a hard gate**: Most small utilities honestly score 0-1 on defensibility. A weak moat lowers the total weighted rank but does not disqualify the candidate.

---

## Criteria and Weights

The scorecard evaluates six criteria. Each criterion is scored on an integer scale from 0 to 3 based on written anchors. The weighted total is computed on a 0-100 scale:

$$\text{Total} = \sum_{c} \text{Weight}_c \times \frac{\text{Score}_c}{3}$$

| Criterion | Weight | Source Theory | Focus |
| :--- | :---: | :--- | :--- |
| `problem_severity` | 20 | Jobs To Be Done (Clayton Christensen) | Real struggling job and user pain |
| `demand_evidence` | 20 | Y Combinator ("Make something people want") | External pull outside the candidate repo |
| `differentiation` | 20 | Equivalence Gap Analysis | Capabilities missing from free OSS alternatives |
| `feasibility` | 15 | Software Architecture & Operability | Code state, dependencies, tests, CI |
| `productization_path` | 15 | Lean Canvas (Ash Maurya) | Customer segment, channel, revenue |
| `moat` | 10 | 7 Powers (Hamilton Helmer) | Structural defensibility (weak moat stays eligible) |
| **Total** | **100** | | |

---

## Written Anchors

### 1. Problem Severity (`problem_severity`) — Weight: 20
**Source Theory**: Clayton Christensen's *Jobs To Be Done* (JTBD). Focuses on whether there is an actual struggling job: what functional, emotional, or social progress an individual is trying to achieve under specific circumstances.

- **Anchor 0 (Trivial / Synthetic)**: Toy project, tutorial homework, or nonexistent problem. No real struggling job exists; users do not experience recurring friction or pain.
- **Anchor 1 (Mild Inconvenience / Nice-to-Have)**: Low-friction issue. The job occurs infrequently or can be bypassed with trivial workarounds (e.g. a 2-line shell script, manual copy-paste, or standard editor command).
- **Anchor 2 (Meaningful Friction)**: Recurring, substantive pain point for an identifiable persona. Manual workarounds are tedious, error-prone, or time-consuming. Solving the job delivers noticeable time savings or operational clarity.
- **Anchor 3 (Acute / Mission-Critical)**: Severe, blocking operational struggle. Users bleed time, revenue, or risk data loss/compliance breach without a solution. High urgency and immediate willingness to switch.

### 2. Demand Evidence (`demand_evidence`) — Weight: 20
**Source Theory**: Y Combinator principle (*"Make something people want"*). Demand must be demonstrated through independent evidence observed **OUTSIDE** the candidate repo (e.g., issues and feature requests on alternative tools, community forum posts, StackOverflow threads, registry downloads of related modules, search volume). The candidate repository's own stars, forks, or issues are never an input.

- **Anchor 0 (Zero External Demand)**: No external signal exists. The candidate repository has only its own commit history or empty search queries with zero external engagement.
- **Anchor 1 (Ambiguous / Passive Signal)**: Uncorrelated registry downloads of generic libraries, or GitHub search totals without active discussion, comments, or demonstrated user intent.
- **Anchor 2 (Verified External Pull)**: At least one qualified external demand row with active user engagement (e.g., a GitHub issue or discussion on an alternative repository with $\ge 2$ comments or $\ge 1$ reaction, an active forum thread, or a maintained related tool showing market activity).
- **Anchor 3 (High-Velocity Multi-Source Pull)**: Multiple independent external signals showing recurring, unmet user pull (e.g., multiple issues/PRs on related projects requesting this exact missing workflow, active community forum threads, and high organic search query interest).

### 3. Differentiation (`differentiation`) — Weight: 20
**Source Theory**: Functional-Equivalence Gap Analysis (`app/agents/equivalence.py`). Evaluates what existing, maintained, free open-source alternatives cannot do.

- **Anchor 0 (Reinvented Wheel / ALREADY_EXISTS)**: No differentiation. Every core capability is already fully covered by a maintained free OSS alternative (status `covered` across all capabilities in the equivalence matrix).
- **Anchor 1 (Cosmetic / Superficial Difference)**: Minor stylistic, configuration, or syntactic differences, but maintained alternatives adequately solve the same functional workflow without meaningful gaps.
- **Anchor 2 (Verified Capability Gap)**: At least one core capability is verified as `missing` or `partial` across all maintained free OSS alternatives. The candidate solves a distinct, unaddressed sub-problem.
- **Anchor 3 (Structural Architectural Advantage)**: Maintained alternatives cannot adopt or solve the candidate's core capability due to fundamental architectural, licensing, or design incompatibilities.

### 4. Feasibility (`feasibility`) — Weight: 15
**Source Theory**: Software Engineering Operability & Codebase State. Evaluates the codebase health, language ecosystem, dependency freshness, test suite, and CI readiness.

- **Anchor 0 (Broken Stub / Abandoned Ruin)**: Non-compiling or broken code, missing source files, obsolete/dead toolchain with no upgrade path, zero tests, failing CI.
- **Anchor 1 (High Technical Debt)**: Prototype or script exists, but lacks test coverage, has failing CI, uses outdated dependencies, or requires extensive manual archaeology to execute.
- **Anchor 2 (Solid Working Baseline)**: Structured code with clear entry points, passing or easily repairable tests, modern or upgradable dependencies, and executable build steps.
- **Anchor 3 (Turnkey Production Foundation)**: Comprehensive test suite, green CI, modular architecture, minimal debt, and immediate runnable state.

### 5. Productization Path (`productization_path`) — Weight: 15
**Source Theory**: Ash Maurya's *Lean Canvas*. Evaluates the clarity of target customer segments, reachable distribution channels, and sustainable monetization/open-core viability.

- **Anchor 0 (No Viable Path)**: Undefined customer persona, no reachable distribution channel, and no plausible monetization or sustainability model.
- **Anchor 1 (Vague General Persona)**: Broad audience label (e.g. "developers") without specific distribution channels, pricing hypothesis, or business model beyond donations.
- **Anchor 2 (Defensible Beachhead & Model)**: Clearly defined niche customer segment, identifiable distribution channel (e.g. VS Code marketplace, package registry, specific community ecosystem), and viable monetization hypothesis (open core, hosted SaaS tier, or enterprise support).
- **Anchor 3 (High-Velocity Go-To-Market)**: Sharp underserved beachhead, organic self-serve distribution flywheel, proven customer willingness to pay, and rapid time-to-value.

### 6. Moat (`moat`) — Weight: 10
**Source Theory**: Hamilton Helmer's *7 Powers* (Scale Economies, Network Economies, Counter-Positioning, Switching Costs, Branding, Cornered Resource, Process Power). Most early-stage utilities honestly score 0 or 1. Moat is not a hard filter: weak moat ranks lower but stays eligible.

- **Anchor 0 (No Moat)**: Easily duplicated by a competent developer in a weekend. No switching costs, network effects, proprietary algorithms, or barriers to entry.
- **Anchor 1 (Nascent Inertia / Friction)**: Mild local switching costs (e.g. local workflow configuration, data file format inertia), but susceptible to fast followers.
- **Anchor 2 (Emerging Power Identified)**: Plausible emergence of at least one named power from the 7 Powers (e.g., Counter-Positioning against an incumbent whose business model prevents adopting this feature, or cumulative data/integration switching costs).
- **Anchor 3 (Durable Structural Power)**: Clear, defensible structural barrier from the 7 Powers (e.g., two-sided network economies, strong cornered resource/license advantage, or substantial scale economies).

---

## Review Procedure

1. **Eligibility Filter**:
   - Verify candidate verdict is `CANDIDATE`.
   - Reject repositories with verdict `ALREADY_EXISTS` or `NO_GO`.
2. **Analyst Scoring**:
   - Score all six criteria 0-3 based on written anchors.
   - Attach verified `evidence_ids` for every score $> 0$.
3. **Deterministic Clamping**:
   - If any criterion has score $> 0$ with no evidence ID present in the supplied evidence set, clamp its score to 0 and flag `UNSUPPORTED`.
4. **Scorecard Review & Rework**:
   - Check for missing criteria, missing evidence, or anchor mismatches.
   - If violations exist, issue rework requests to send work back for re-investigation.
5. **Protagonist Selection**:
   - Eligible candidates must have clamped `feasibility >= 2`.
   - Select candidate with highest weighted total (0-100).
   - Break ties by `repo_id` ascending.
   - If no candidate qualifies, return `None` with an explanatory reason.
