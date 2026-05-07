# Story 5.7: GitHub API adapter (PR draft + retries)

Status: ready-for-dev

## Story

As the platform,
I want a GitHub adapter in `services/worker-wrapper/adapters/github_client.py` that creates PR drafts, adds commits/branches, and uses `tenacity` 3x exponential-backoff retries,
So that PR creation is reliable on flaky networks without infinite retry loops.

## Acceptance Criteria

1. **AC-1: PR draft creation** — `GitHubClient` adapter at `services/worker-wrapper/src/worker_wrapper/adapters/github_client.py` calls GitHub REST API `POST /repos/{owner}/{repo}/pulls` with `draft: true`, correct `title`, `head`, `base`, and `body` metadata. Returns a structured `PRDraftResult` dataclass. Authenticates via `GITHUB_TOKEN` from `WorkerSettings`.

2. **AC-2: Retry resilience** — All GitHub API calls use `tenacity` with 3x exponential-backoff + jitter for 5xx/timeout/network errors. Total timeout per call <= 10 s. 4xx errors (except 429) are NOT retried. Idempotency key passed via `GitHub-Idempotency-Key` header for `git push` replay scenarios.

3. **AC-3: Async-only HTTP** — Uses `aiohttp` (or stdlib `urllib` wrapped with `asyncio.to_thread`) for HTTP calls. No synchronous `requests` import anywhere. `scripts/check_imports.py` exits 0.

4. **AC-4: Branch operations** — `create_branch(owner, repo, ref, sha)` method calls `POST /repos/{owner}/{repo}/git/refs`. Returns structured result. Same retry/timeout policies as PR creation.

5. **AC-5: Structured results** — `PRDraftResult` and `BranchResult` dataclasses (in the adapter file) with fields: `success: bool`, `url: str | None`, `number: int | None`, `error: str | None`. No exceptions propagated for expected failures (rate limits, auth errors, 404s).

6. **AC-6: Config via pydantic-settings** — `GITHUB_TOKEN` and `GITHUB_API_BASE_URL` fields added to `WorkerSettings` in `app/config.py`. Token is `SecretStr` to prevent accidental logging. No direct `os.environ` access.

7. **AC-7: Secret hygiene** — Every access to `GITHUB_TOKEN` is logged via structlog with the token **redacted**. The adapter never logs raw token values. `scan_text()` from `secret_hygiene` is NOT needed here (the adapter doesn't write files) but the token must never appear in event payloads or log output.

8. **AC-8: Adapter layer discipline** — `github_client.py` lives in `adapters/`. Domain code never imports it directly. The adapter uses structlog (same as `claude_code_runner.py` and `mcp_clients.py`), NOT stdlib `logging`. `scripts/check_imports.py` exits 0.

9. **AC-9: Tests** — At least 12 new tests in `test_github_client.py` (co-located in adapters/): happy-path PR creation, branch creation, retry on 5xx, no retry on 4xx, auth error handling, timeout handling, rate-limit handling, empty token validation, idempotency-key header, config loading. Tests mock HTTP calls (no live GitHub API).

10. **AC-10: Dependencies** — `aiohttp` and `tenacity` added to `services/worker-wrapper/pyproject.toml` dependencies. `uv sync` succeeds.

11. **AC-11: `just lint` 9/9 green** — All lint gates pass, including `mypy --strict`.

12. **AC-12: `just test` no regressions** — Existing test count unchanged. New tests increase the count.

13. **AC-13: Atomic commit** — title: `feat(worker-wrapper): add GitHub API adapter with tenacity retries · E5`

## Tasks / Subtasks

- [ ] **Task 1: Add dependencies** (AC: #10)
  - [ ] Add `aiohttp>=3.9` and `tenacity>=8.2` to `services/worker-wrapper/pyproject.toml`
  - [ ] Run `uv sync` to verify resolution

- [ ] **Task 2: Extend config** (AC: #6)
  - [ ] Add `github_token: SecretStr = SecretStr("")` to `WorkerSettings`
  - [ ] Add `github_api_base_url: str = "https://api.github.com"` to `WorkerSettings`
  - [ ] Add `github_timeout_s: float = 10.0` to `WorkerSettings`
  - [ ] Validate: `WORKER_GITHUB_TOKEN` env var mapping works

- [ ] **Task 3: Define result dataclasses** (AC: #5)
  - [ ] `PRDraftResult` dataclass: `success`, `url`, `number`, `error`
  - [ ] `BranchResult` dataclass: `success`, `ref`, `error`

- [ ] **Task 4: Implement `GitHubClient`** (AC: #1, #2, #3, #4, #7)
  - [ ] `class GitHubClient` in `adapters/github_client.py`
  - [ ] Constructor takes `WorkerSettings`, extracts token/base_url/timeout
  - [ ] `async create_pr_draft(owner, repo, title, head, base, body) -> PRDraftResult`
  - [ ] `async create_branch(owner, repo, ref, sha) -> BranchResult`
  - [ ] `tenacity.retry` decorator: `stop=stop_after_attempt(3)`, `wait=wait_exponential(multiplier=0.5, max=5) + wait_random(0, 0.5)`, `retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))`
  - [ ] Per-call timeout via `asyncio.wait_for(..., timeout=settings.github_timeout_s)`
  - [ ] `GitHub-Idempotency-Key` header on POST requests
  - [ ] Structured error handling: catch `aiohttp.ClientResponseError`, return result dataclass
  - [ ] Token redacted in all structlog calls

- [ ] **Task 5: Write tests** (AC: #9)
  - [ ] Create `test_github_client.py` in adapters/
  - [ ] Use `aiohttp.test_utils.AioHTTPTestCase` or `aioresponses` to mock HTTP
  - [ ] Test: happy-path PR creation (201 response)
  - [ ] Test: branch creation (201 response)
  - [ ] Test: retry on 500 (mock 3 failures then success)
  - [ ] Test: no retry on 400/401/403/404/422
  - [ ] Test: retry on 429 (rate limit)
  - [ ] Test: timeout triggers retry
  - [ ] Test: empty token returns error without HTTP call
  - [ ] Test: idempotency-key header present
  - [ ] Test: token never in log output
  - [ ] Test: config loading from WorkerSettings

- [ ] **Task 6: Verification + commit** (AC: #8, #11, #12, #13)
  - [ ] `mypy --strict` clean on all modified files
  - [ ] `ruff check` clean
  - [ ] `scripts/check_imports.py` exits 0
  - [ ] `just test` — no regressions
  - [ ] Atomic commit

## Dev Notes

### What already exists

The worker-wrapper service at `services/worker-wrapper/` has:
- `adapters/claude_code_runner.py` — subprocess supervision, takes `WorkerSettings`, uses structlog, returns `ClaudeCodeResult` dataclass
- `adapters/mcp_clients.py` — MCP client group, takes `WorkerSettings`, uses structlog, async context manager
- `app/config.py` — `WorkerSettings(BaseSettings)` with `env_prefix="WORKER_"`
- `pyproject.toml` — deps: `mcp`, `structlog`, `pydantic-settings`, `secret-hygiene`, `events` (workspace)
- No `aiohttp` or `tenacity` yet — must add both

### Adapter patterns to follow

1. **Constructor injection**: `def __init__(self, settings: WorkerSettings)` — same as `ClaudeCodeRunner` and `MCPClientGroup`
2. **structlog**: `structlog.get_logger(__name__)` — NOT stdlib `logging`
3. **Result dataclasses**: Return structured results, not exceptions — same as `ClaudeCodeResult`
4. **No abstract base classes**: All adapters are concrete classes — no Protocol/ABC
5. **Best-effort error handling**: Log + return error result, don't crash
6. **Async context manager**: Consider `__aenter__`/`__aexit__` for `aiohttp.ClientSession` lifecycle (same pattern as `MCPClientGroup`)

### Import-graph rules

| Import | Allowed? | Notes |
|---|---|---|
| `aiohttp` | ALLOWED | adapter layer — IO is adapters' job |
| `tenacity` | ALLOWED | adapter layer — retry at adapter boundary only |
| `structlog` | ALLOWED | same as other adapters |
| `pydantic` / `pydantic_settings` | ALLOWED | config pattern |
| `asyncio` | ALLOWED | async adapter |
| `requests` (sync) | **FORBIDDEN** | AC-2 explicitly bans sync HTTP client |
| Domain modules | **FORBIDDEN** | adapter must not import from domain/ |
| Other services | **FORBIDDEN** | cross-service import ban |

### GitHub API endpoints needed

| Operation | Method | Endpoint | Notes |
|---|---|---|---|
| Create PR draft | POST | `/repos/{owner}/{repo}/pulls` | Body: `{"title": ..., "head": ..., "base": ..., "body": ..., "draft": true}` |
| Create branch ref | POST | `/repos/{owner}/{repo}/git/refs` | Body: `{"ref": "refs/heads/...", "sha": "..."}` |

### Authentication

- `Authorization: Bearer {GITHUB_TOKEN}` header on every request
- `Accept: application/vnd.github+json` header
- Token must be `SecretStr` in config to prevent accidental exposure in repr/log
- `settings.github_token.get_secret_value()` to extract for API calls

### Retry strategy

```python
retry_decorator = tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=0.5, max=5) + tenacity.wait_random(0, 0.5),
    retry=tenacity.retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
)
```

- 5xx and network errors: retry up to 3x
- 4xx (except 429): do NOT retry — these are client errors
- 429 (rate limit): retry after `Retry-After` header (tenacity can handle this)
- Total timeout per call: `asyncio.wait_for(..., timeout=10.0)` wrapping the tenacity-decorated call

### Idempotency key

- `GitHub-Idempotency-Key` header on POST requests
- Architecture line 833: "git push replay (GitHub-idempotency-key passed through to adapter)"
- Generate UUIDv7 via `events.ids` package for the key
- Purpose: if `git push` is replayed after restart, the same PR won't be created twice

### Downstream consumers

- **Story 5.12** (task execution driver) — will call `github_client.create_pr_draft(...)` when task reaches green-tests state
- **Story 5.14** (PR draft auto-creation) — explicitly uses this adapter: `github_client.create_pr_draft(...)`
- **Story 5.17b** (cross-restart approval) — needs idempotency key for replay scenarios
- **Story 6.1** (capability tier enforcement) — PR creation is Tier 2 (repo mutation)

### Contract test placeholder

Architecture line 760 lists `tests/contract/test_github_api_contract.py` as a required contract test. This story creates the adapter but the **contract test** (with recorded fixtures) can be deferred to Story 5.14 when the actual emission flow is wired. The unit tests in this story mock HTTP calls.

### Key patterns from previous stories

1. **pydantic-settings**: `BaseSettings` with `env_prefix` — all env vars prefixed `WORKER_`
2. **`SecretStr`**: Used for `WORKER_ANTHROPIC_API_KEY` already in `WorkerSettings` — follow same pattern
3. **structlog keyword style**: `log.info("event_name", key=value)` — first arg is event name
4. **Workspace deps**: `events` and `secret-hygiene` are workspace packages — add `aiohttp` and `tenacity` as regular (non-workspace) deps
5. **`@dataclass` for results**: Same as `ClaudeCodeResult`, `FileEditResult`, `EditValidation`

### References

- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1533-1549 — Story 5.7 definition]
- [Source: `_bmad-output/planning-artifacts/epics.md` lines 1643-1655 — Story 5.14 downstream consumer]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 57 — GitHub REST API external dependency]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 426 — tenacity retry policy]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 683-698 — worker-wrapper directory tree]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 760 — contract test placeholder]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 782 — external API boundary]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 833 — idempotency-key for git push replay]
- [Source: `_bmad-output/planning-artifacts/architecture.md` lines 851-859 — external integrations table]
- [Source: `_bmad-output/planning-artifacts/architecture.md` line 914 — contract test pattern]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 821 — FR10 PR draft auto-creation]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 872 — FR42 secret.accessed events]
- [Source: `_bmad-output/planning-artifacts/prd.md` line 921 — NFR-S1 zero plaintext secrets]
- [Source: `services/worker-wrapper/src/worker_wrapper/adapters/claude_code_runner.py` — adapter pattern]
- [Source: `services/worker-wrapper/src/worker_wrapper/adapters/mcp_clients.py` — async context manager pattern]
- [Source: `services/worker-wrapper/src/worker_wrapper/app/config.py` — WorkerSettings pattern]
- [Source: `services/worker-wrapper/pyproject.toml` — existing dependencies]
