# Ralplan Critic Review — Story 127.1 Cycle 1

Verdict: REQUEST_CHANGES
Architectural Status: BLOCK
Agent role: critic
Agent id: 019f22e8-59fc-7650-a79e-c84354ecea69

Blockers:
1. Exact `q` length/encoding values are not pinned despite Story 127.1 requiring lengths and encoding.
2. `field=status` and existing `status=` selector interaction is ambiguous.
3. Verification lacks assertions for those exact rules and lacks a docs-only changed-file allowlist.

Required repairs:
- Add exact raw byte cap and per-field caps.
- Define raw spelling/encoding policy including percent-encoding, spaces, controls, normalization, empty values.
- Define fail-closed `field=status` plus `status=` interaction, preferably reject both.
- Extend verification for exact values/rules and docs-only changed-file allowlist.
