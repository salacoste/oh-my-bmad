# Story 125.4 — Dashboard Wiring Inventory/Test Guard

Status: implemented locally
Scope: dashboard inventory and behavior-preserving regression guards only; no broad rewiring
Context snapshot: `.omx/context/story-125-3-search-discovery-125-4-inventory-test-guard-20260701T161249Z.md`
Deep-interview handoff: `.omx/interviews/story-125-3-125-4-deep-interview-complete-20260701T161249Z.md`
Ralplan plan: `.omx/plans/story-125-3-125-4-inventory-test-guard-plan-20260701T161454Z.md`
Test spec: `.omx/specs/story-125-3-125-4-inventory-test-guard-test-spec-20260701T161454Z.md`
Architect review: `.omx/artifacts/ralplan/story-125-3-125-4-architect-review.md`
Critic review: `.omx/artifacts/ralplan/story-125-3-125-4-critic-review.md`

## Decision

Story 125.4 starts broad dashboard cleanup with inventory and behavior-preserving test guards only. It does not perform dashboard runtime cleanup, source rewiring, API/backend behavior changes, browser behavior changes, dependency changes, or production operations.

## Parseable inventory

```json
{
  "schema_version": 1,
  "story": "125.4",
  "status": "implemented_locally",
  "runtime_rewiring_authorized": false,
  "contract_source": "derived_guard",
  "shared_dashboard_guard": true,
  "shared_guard_update_policy": "intentional future dashboard shell/runtime changes must update this inventory and its guard tests in the same narrow story",
  "static_shell": {
    "path": "dashboard/static/index.html",
    "approved_scripts": [
      "health-readiness.js",
      "task-detail.js",
      "aggregate-task-list.js",
      "session-list.js",
      "session-detail.js",
      "event-timeline.js",
      "trace-correlation.js",
      "history-replay.js",
      "lifecycle-snapshot.js",
      "task-log-digest.js",
      "digest-stream.js"
    ],
    "inline_scripts_authorized": false,
    "modulepreload_authorized": false
  },
  "aggregate_task_list": {
    "runtime_path": "dashboard/static/aggregate-task-list.js",
    "test_path": "tests/dashboard/test_aggregate_task_list_runtime_boundary.py",
    "approved_route_patterns": [
      "GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}",
      "GET /v1/tasks?sort={task_sort}"
    ],
    "approved_fetch_base": "/v1/tasks",
    "visible_control_ids": [
      "aggregate-task-list-status-control",
      "aggregate-task-list-limit-control",
      "aggregate-task-list-offset-control",
      "aggregate-task-list-load",
      "aggregate-task-list-previous-offset",
      "aggregate-task-list-next-offset",
      "aggregate-task-list-sort-control",
      "aggregate-task-list-sort-load"
    ],
    "metadata_target_ids": [
      "aggregate-task-list-status",
      "aggregate-task-list-source",
      "aggregate-task-list-selected-status",
      "aggregate-task-list-selected-limit",
      "aggregate-task-list-selected-offset",
      "aggregate-task-list-freshness",
      "aggregate-task-list-authority",
      "aggregate-task-list-provenance",
      "aggregate-task-list-correlation",
      "aggregate-task-list-pagination",
      "aggregate-task-list-degraded",
      "aggregate-task-list-count",
      "aggregate-task-list-rows",
      "aggregate-task-list-sort-status",
      "aggregate-task-list-sort-source",
      "aggregate-task-list-sort-selected-sort",
      "aggregate-task-list-sort-freshness",
      "aggregate-task-list-sort-authority",
      "aggregate-task-list-sort-provenance",
      "aggregate-task-list-sort-correlation",
      "aggregate-task-list-sort-pagination",
      "aggregate-task-list-sort-degraded",
      "aggregate-task-list-sort-count",
      "aggregate-task-list-sort-rows"
    ],
    "authorized_sort_values": [
      "updated_at_desc_id_asc",
      "created_at_desc_id_asc"
    ],
    "status": "live_guarded"
  },
  "runtime_modules": [
    {"script": "health-readiness.js", "boundary": "GET /v1/health", "status": "live_guarded"},
    {"script": "task-detail.js", "boundary": "GET /v1/tasks/{task_id}", "status": "live_guarded"},
    {"script": "aggregate-task-list.js", "boundary": "approved aggregate task-list reads only", "status": "live_guarded"},
    {"script": "session-list.js", "boundary": "GET /v1/sessions", "status": "live_guarded"},
    {"script": "session-detail.js", "boundary": "GET /v1/sessions/{session_id}", "status": "live_guarded"},
    {"script": "event-timeline.js", "boundary": "task-scoped events/transitions read", "status": "live_guarded"},
    {"script": "trace-correlation.js", "boundary": "GET /v1/trace/{trace_id}", "status": "live_guarded"},
    {"script": "history-replay.js", "boundary": "history/replay readiness reads", "status": "live_guarded"},
    {"script": "lifecycle-snapshot.js", "boundary": "lifecycle snapshot readiness/create surfaces", "status": "live_guarded"},
    {"script": "task-log-digest.js", "boundary": "GET /v1/tasks/{task_id}/logs/digest", "status": "live_guarded"},
    {"script": "digest-stream.js", "boundary": "GET /v1/tasks/{task_id}/logs/digest/stream", "status": "live_guarded"}
  ],
  "search_discovery": {
    "runtime_authorized": false,
    "contract_artifact": "_bmad-output/implementation-artifacts/125-3-task-list-search-discovery-implementation-planning.md",
    "forbidden_markers": [
      "/v1/tasks/search",
      "task-search",
      "discover",
      "q=",
      "cursor=",
      "page=",
      "hidden selectors",
      "automatic traversal",
      "URL/hash/storage selectors",
      "generated live data"
    ],
    "forbidden_authorization_policy": "all listed markers remain unauthorized unless a separate future product/architecture contract explicitly selects them"
  },
  "broad_cleanup": {
    "runtime_rewiring_authorized": false,
    "future_cleanup_policy": "future stories must be narrow file-level, behavior-preserving slices guarded by this shared inventory before edits",
    "forbidden_current_story_changes": [
      "dashboard runtime rewiring",
      "dashboard runtime cleanup",
      "dashboard JavaScript behavior changes",
      "dashboard HTML wiring changes",
      "backend/API behavior changes",
      "browser behavior changes",
      "generated data",
      "hidden selectors",
      "automatic traversal",
      "dependencies or lockfiles",
      "services/MCP changes",
      "CI/deployment changes",
      "credentials or production operations"
    ]
  }
}
```

## Live vs deferred classification

- Live guarded: the eleven static dashboard scripts and their already-approved route/read boundaries listed above.
- Deferred: task-list search/discovery runtime, arbitrary query grammar, hidden selectors, automatic traversal, generated live data, and broad dashboard cleanup.
- Not a cleanup target in this story: runtime JavaScript, HTML wiring, backend/API routes, services, dependencies, lockfiles, CI/deployment, credentials, and production operations.

## Shared guard intent

This inventory intentionally becomes a shared dashboard guard for future cleanup stories. It is not a runtime source and does not authorize rewiring. Future intentional dashboard shell/runtime changes must update this inventory and the guard tests in the same narrow, behavior-preserving story.

## Test guards

`tests/dashboard/test_dashboard_wiring_inventory.py` parses the structured JSON fields and compares them to the current static shell and dashboard runtime file inventory. Existing aggregate-task-list runtime tests remain the behavioral guard for approved route/control/fail-closed behavior.
