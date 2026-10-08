# Sample Evidence

Sample mode must use the same PhoenixRepo reasoning pipeline as live mode. The only difference is the evidence source.

```text
SAMPLE_MODE=true
    -> Cached Evidence
    -> Normal Evidence Pipeline
    -> Evidence Graph
    -> Normal Reasoning
    -> Normal Decision
    -> Normal Output
```

Sample mode must never bypass reasoning by returning hardcoded final decisions.
