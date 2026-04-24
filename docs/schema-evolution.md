# Schema evolution

How to evolve the event-log schema without breaking downstream replay.
The event log is the platform's source of truth; every schema change must be
backwards-compatible or accompanied by a one-shot migrator that transforms the
log before any new code reads it.

---

## Additive-only within major schema

**NFR-M3 rule (Architecture §Category 1):** within a major schema version,
only additive changes are permitted. Fields may be added; existing fields may
not be removed or renamed.

**Rationale:** The event log is a replay source. Any service that reconstitutes
state by replaying events (the registry materializer, Story 2.5; snapshot
replay, Story 2.6) must produce identical state regardless of when replay runs.
Removing or renaming a field in a schema version that already has live data
silently breaks older replays. Additive-only ensures a reader written against
v1.0.0 can still parse a v1.0.1 envelope — it ignores fields it does not know.

---

## Add a new event type

Follow this sequence when you need to emit a new event that has no prior art
in the REGISTRY:

1. **Decide the event name.** Use past-tense verb form in dot-separated
   namespace: `<domain>.<object>.<verb>`. Examples: `task.plan.committed`,
   `task.budget.exceeded`, `registry.snapshot.captured`.

2. **Add to the REGISTRY frozenset** in
   `packages/events/src/events/schema_registry.py`:
   ```python
   REGISTRY: frozenset[str] = frozenset({
       "task.plan.committed",   # your new type
   })
   ```
   The frozenset is append-only. Never remove an entry once it ships.

3. **Define the Pydantic payload model** in the story that owns the new event.
   Co-locate it with the emission site's service or package — not in
   `packages/events`, which only owns the envelope and the registry. Example:
   ```python
   from pydantic import BaseModel

   class TaskPlanCommittedPayload(BaseModel):
       task_id: str
       plan_hash: str
   ```

4. **Register in the schema-registry map.** Story 2.1 lands the full
   `EVENT_SCHEMAS` map that ties each type string to its payload model.
   Until then, the registry is a frozenset only — the CI gate checks membership,
   not model binding.

5. **Add the emission site** using the platform's `emit_event` helper
   (arrives Story 2.4):
   ```python
   emit_event(type="task.plan.committed", payload=TaskPlanCommittedPayload(
       task_id=task_id,
       plan_hash=plan_hash,
   ))
   ```

6. **Run `just lint`** — `scripts/check_event_registry.py` walks every
   `type=` literal at emission sites and fails if any literal is absent from
   the REGISTRY frozenset. A clean lint confirms the new type is properly
   registered.

---

## Ship a migrator

When a breaking change is unavoidable (field removed, type changed, field
renamed), ship a one-shot migrator that transforms the existing event log
before any service that reads the new schema is deployed.

### Directory layout

Create a new migration function in `scripts/migrator/src/migrator/__main__.py`
and register it in the `MIGRATIONS` dict:

```
scripts/migrator/
  Dockerfile
  pyproject.toml
  src/migrator/
    __init__.py
    __main__.py        ← add migration function + MIGRATIONS entry here
  tests/
    fixtures/
      sample_v<from>.jsonl   ← synthetic fixture for testing
    assert_migrated.py       ← assertion script for just migrator-test-additive
```

### Migration function shape

```python
from typing import Any

def migrate_v1_0_1_to_v1_1_0(event: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(event)
    migrated["schema_version"] = "1.1.0"
    # add/remove fields as per the migration spec
    return migrated
```

Register it:

```python
MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "v1.0.0-to-v1.0.1": migrate_v1_0_0_to_v1_0_1,   # shipped Story 1.3
    "v1.0.1-to-v1.1.0": migrate_v1_0_1_to_v1_1_0,   # your new migration
}
```

The function receives one parsed event dict and returns the migrated dict.
Raise an exception to abort the entire migration — the atomic write pattern
ensures the original log is untouched on failure.

---

## Run the migrator

Build the migrator image and invoke it via compose:

```sh
docker compose run --rm migrator <from-version>-to-<to-version>
```

Example (shipped template — Story 1.3):

```sh
docker compose run --rm migrator v1.0.0-to-v1.0.1
```

**What happens internally:**

1. The migrator reads `$EVENT_LOG_PATH`
   (default `/var/lib/oh-my-bmad/registry/events/current.jsonl`).
2. Each line is parsed, migrated, and written to a `.partial` staging file.
3. On success: the staging file is `fsync`-ed then `os.replace`-ed to
   `current.v<to>.jsonl` (atomic rename — the final file either fully exists
   or doesn't).
4. The original `current.jsonl` is moved to `current.v<from>.archive`.
5. Any crash mid-write leaves `.partial` and the original log untouched — retry
   is safe.

Stop the stack before running the migrator to avoid concurrent writes:

```sh
docker compose down
docker compose run --rm migrator v1.0.0-to-v1.0.1
docker compose up -d
```

---

## Roll back

If the migrated log causes issues, restore the pre-migration archive:

```sh
# 1. Stop the stack.
docker compose down

# 2. Shell into a throwaway container with the data volume mounted.
docker run --rm -it \
    -v oh-my-bmad_oh-my-bmad-data:/data \
    alpine:3 sh

# 3. Inside the container: rename archive back to current.jsonl.
#    Replace <FROM_VERSION> with the version you rolled back from.
mv /data/registry/events/current.v<FROM_VERSION>.archive \
   /data/registry/events/current.jsonl
exit

# 4. Restart on the old image tag (roll back OMB_VERSION in .env first).
docker compose up -d
```

The archive file is the sole recovery asset for a roll-back. Confirm it is
present before running a migration, and include it in your next backup
(`just backup`) immediately after a successful migration.

---

## See also

- [Operator runbook](./operator-runbook.md) — SQLite WAL recovery + per-service restarts.
- [Backup / restore](./backup-restore.md) — volume snapshot before any migration.
