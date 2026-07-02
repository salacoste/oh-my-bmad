# Critic Review — Story 127.2 Cycle 1

Status: BLOCK

Required repairs:
- Use literal search semantics, not SQL wildcard matching. Add `_` non-wildcard tests.
- Semantically parse timestamps and reject invalid calendar/time values.
- Pin `last_event_type` to the current `Task.last_event_id` event only; do not inspect history/payloads/summaries.
- Enforce full raw query length `1..256` before decoded params are trusted and exact byte-level shape matrix.
- Cover duplicate status semantics for every suffix family.
- Specify exact search response fields and exact `redaction_state` literal.
- Add field max/max+1, raw query 256/257, q 1/0, timestamp, wildcard, composition, and denied-field tests.
