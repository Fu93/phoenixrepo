from state.machine import PhoenixState

REQUIRED_EVIDENCE = {
    PhoenixState.RECONNAISSANCE_COMPLETE: ["repository_structure"],
    PhoenixState.EVIDENCE_COMPLETE: ["source_evidence", "documentation_evidence"],
    PhoenixState.INTENT_RECONSTRUCTED: ["intent_evidence"],
    PhoenixState.MARKET_ANALYZED: ["market_evidence"],
    PhoenixState.VALUE_DECISION: [
        "intent_evidence",
        "market_evidence",
        "salvageability_evidence",
    ],
}
