# Story 128.6 — Dashboard Shared Helper Expansion After Panel Slices

Status: done locally on 2026-07-04.

## Decision
After Stories 128.3-128.5 passed focused runtime boundary tests, helper expansion stayed module-local. A new shared runtime helper script was intentionally not added because the existing dashboard tests assert exact script order/module graph, and adding a shared browser module would create broad shell/inventory churn without improving route/selector/mutation safety.

## Cleanup completed
- Duplicated read-failure and visible-source helper patterns were extracted locally within the bounded panel modules.
- The aggregate task-list helper seed from Story 128.2 remains aggregate-local.
- No shared helper file, new script tag, dependency, DOM reader, fetch wrapper, route builder, stateful cache, or cross-panel runtime coupling was introduced.

## Verification
- Focused Story 128.3, 128.4, and 128.5 runtime boundary suites passed after local helper extraction.
- Exact runtime module allowlist tests continue to pass, proving no new shared runtime module was added.


## Panel-local state vocabulary guard
Shared helper extraction intentionally did not normalize failure-state spelling across panels. Existing route families already expose panel-local contracts such as `backend unavailable` for legacy task/event/trace/history/lifecycle panels, `backend-unavailable` for session/digest panels, and `not-found` for session detail. Story 128.6 preserves those strings rather than centralizing them because changing them would alter user-visible runtime boundary contracts. Focused runtime tests and the dashboard wiring inventory guard protect the exact panel-local vocabulary.
