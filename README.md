# PhoenixRepo

PhoenixRepo does not assume abandoned software should be saved. It investigates whether it deserves to live again. Every decision must be traceable to evidence.

Pre-event skeleton for Open Agent Hackathon 2026, Track 02: The Agent That Can Explain Why. Commits before 22 October 2026 00:00 UTC are a declared pre-existing component. They do not contain a resurrection run.

## What this skeleton is

- `POST /run`, JSON in, JSON out, bound to `0.0.0.0`
- Docker image that installs dependencies at build time
- Sample mode that does not call partner APIs and does not return a stored answer
- Three example slots
- Log directory created at startup

## What this skeleton is not

- An evidence ingestion path
- A GO/NO-GO pipeline
- A builder or a prover
- A claim that Zetaris or Meterless are integrated

See `PRE_EVENT_PLAN.md` for the locked boundary.

## What this skeleton is

- Entry point shape judges asked for: `POST /run`, JSON in, JSON out
- Docker image that installs dependencies at build time
- Sample mode that does not call partner APIs
- Three empty example slots
- Log directory created at startup

## What this skeleton is not

- An archaeologist, a market judge, or a builder
- A claim that Zetaris or Meterless are integrated yet
- A demo

## Setup

```bash
cp .env.example .env
docker build -t phoenixrepo .
docker run --rm -p 8000:8000 --env-file .env phoenixrepo
```

Without credentials:

```bash
docker run --rm -p 8000:8000 -e SAMPLE_MODE=true -e PORT=8000 phoenixrepo
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d @input_examples/example_1.json
```

## Track

The Agent That Can Explain Why. One builder.

## AI and model usage

No model is called by this skeleton. When the build window opens, reasoning is planned on xAI Grok. Partner adapters for Zetaris and Meterless are stubs until those credentials exist.

## Limitations

`/run` returns a structured `not_implemented` payload. That is intentional.
