# Security boundary

Historical pre-event note, written for the state before 2026-10-22 00:00 UTC. Zetaris and Meterless are no longer interface-only: see `README.md` for the current gate-1 behaviour. Where this file disagrees with the README, the README is current.

SAMPLE_MODE uses the normal pipeline and cached sample evidence. It does not call sponsors and does not require the network.

Secrets stay in the environment and settings. They are not stored in evidence or evidence packs. Trace metadata redacts api_key, authorization, token, secret, and password.

Zetaris and Meterless are interface-only before 2026-10-22 00:00 UTC. Their status says INTERFACE_ONLY, not connected.
