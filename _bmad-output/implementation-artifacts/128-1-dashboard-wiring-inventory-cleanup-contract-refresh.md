# Story 128.1 — Dashboard Wiring Inventory and Cleanup Contract Refresh

Status: implemented locally
Scope: post-Epic-127 dashboard wiring inventory, cleanup contract, and bidirectional drift tests only; no runtime rewiring
Context snapshot: `.omx/context/epic-128-story-128-1-dashboard-wiring-inventory-20260703T142322Z.md`
Deep-interview handoff: `.omx/interviews/story-128-1-dashboard-wiring-inventory-deep-interview-complete.md`
Ralplan plan: `.omx/plans/story-128-1-dashboard-wiring-inventory-plan.md`
Test spec: `.omx/specs/story-128-1-dashboard-wiring-inventory-test-spec.md`
Architect review: `.omx/artifacts/ralplan/story-128-1-architect-review-cycle-2.md`
Critic review: `.omx/artifacts/ralplan/story-128-1-critic-review-cycle-2.md`

## Decision

Story 128.1 refreshes the dashboard wiring inventory after Epic 127 search/discovery and explicit bounded traversal. It does not perform dashboard runtime cleanup, source rewiring, backend/API behavior changes, browser behavior changes, dependency changes, credential changes, deployment changes, or production operations.

The inventory is a factual cleanup contract for later Epic 128 slices. Runtime surfaces are classified as live guarded, deferred, or forbidden, and every module/control/route entry records owner story and phase.

## Parseable inventory

```json
{
  "schema_version": 2,
  "story": "128.1",
  "status": "implemented_locally",
  "generated_at": "2026-07-03T14:50:00Z",
  "runtime_rewiring_authorized": false,
  "contract_source": "post_epic_127_fact_inventory",
  "shared_dashboard_guard": true,
  "shared_guard_update_policy": "intentional future dashboard shell/runtime changes must update this inventory and bidirectional guard tests with exact source-marker plus fetch-signature coverage in the same narrow story",
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
    "modulepreload_authorized": false,
    "owner_story": "128.1",
    "owner_phase": "Phase 48",
    "classification": "live_guarded_static_shell"
  },
  "runtime_modules": [
    {
      "script": "health-readiness.js",
      "runtime_path": "dashboard/static/health-readiness.js",
      "classification": "live_guarded",
      "owner_story": "101.2",
      "owner_phase": "Phase 22",
      "routes": [
        {
          "name": "health-readiness",
          "pattern": "GET /v1/health",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "101.2",
          "owner_phase": "Phase 22",
          "selector_source": "none",
          "credentials": "default",
          "headers": [
            "Accept: application/json"
          ],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "\"/v1/health\"",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const ROUTE = \"/v1/health\"",
            "fetch(\"/v1/health\""
          ]
        }
      ],
      "visible_control_ids": [],
      "metadata_target_ids": [
        "health-readiness-status",
        "health-readiness-source",
        "health-readiness-freshness",
        "health-readiness-authority",
        "health-readiness-detail"
      ],
      "visible_source_ids": [],
      "side_channel_policy": {
        "classification": "forbidden_absent",
        "forbidden": [
          "EventSource",
          "WebSocket",
          "XMLHttpRequest",
          "localStorage",
          "sessionStorage",
          "document.cookie",
          "location.hash",
          "Worker",
          "serviceWorker",
          "new Worker",
          "SharedWorker"
        ],
        "allowed": [
          "DOMContentLoaded explicit startup only"
        ]
      },
      "dom_surfaces": [
        {
          "id": "health-readiness-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "101.2",
          "owner_phase": "Phase 22"
        },
        {
          "id": "health-readiness-source",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "101.2",
          "owner_phase": "Phase 22"
        },
        {
          "id": "health-readiness-freshness",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "101.2",
          "owner_phase": "Phase 22"
        },
        {
          "id": "health-readiness-authority",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "101.2",
          "owner_phase": "Phase 22"
        },
        {
          "id": "health-readiness-detail",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "101.2",
          "owner_phase": "Phase 22"
        }
      ],
      "passive_global_sources": []
    },
    {
      "script": "task-detail.js",
      "runtime_path": "dashboard/static/task-detail.js",
      "classification": "live_guarded",
      "owner_story": "102.2",
      "owner_phase": "Phase 23",
      "routes": [
        {
          "name": "task-detail",
          "pattern": "GET /v1/tasks/{task_id}",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "102.2",
          "owner_phase": "Phase 23",
          "selector_source": "visible_source:task-detail-task-id-source",
          "credentials": "default",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const ROUTE_PATTERN = \"GET /v1/tasks/{task_id}\"",
            "const ROUTE_PREFIX = \"/v1/tasks/\"",
            "fetch(route"
          ]
        }
      ],
      "visible_control_ids": [],
      "metadata_target_ids": [
        "task-detail-status",
        "task-detail-source",
        "task-detail-task-id",
        "task-detail-freshness",
        "task-detail-authority",
        "task-detail-detail"
      ],
      "visible_source_ids": [
        "task-detail-task-id-source"
      ],
      "side_channel_policy": {
        "classification": "forbidden_absent",
        "forbidden": [
          "EventSource",
          "WebSocket",
          "XMLHttpRequest",
          "localStorage",
          "sessionStorage",
          "document.cookie",
          "location.hash",
          "Worker",
          "serviceWorker",
          "new Worker",
          "SharedWorker"
        ],
        "allowed": [
          "DOMContentLoaded explicit startup only"
        ]
      },
      "dom_surfaces": [
        {
          "id": "task-detail-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "102.2",
          "owner_phase": "Phase 23"
        },
        {
          "id": "task-detail-source",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "102.2",
          "owner_phase": "Phase 23"
        },
        {
          "id": "task-detail-task-id",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "102.2",
          "owner_phase": "Phase 23"
        },
        {
          "id": "task-detail-freshness",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "102.2",
          "owner_phase": "Phase 23"
        },
        {
          "id": "task-detail-authority",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "102.2",
          "owner_phase": "Phase 23"
        },
        {
          "id": "task-detail-detail",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "102.2",
          "owner_phase": "Phase 23"
        },
        {
          "id": "task-detail-task-id-source",
          "role": "visible_source",
          "classification": "live_visible_source",
          "owner_story": "102.2",
          "owner_phase": "Phase 23"
        }
      ],
      "passive_global_sources": []
    },
    {
      "script": "aggregate-task-list.js",
      "runtime_path": "dashboard/static/aggregate-task-list.js",
      "classification": "live_guarded",
      "owner_story": "127.4",
      "owner_phase": "Phase 48",
      "routes": [
        {
          "name": "aggregate-full-selector-composition",
          "pattern": "GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "126.2",
          "owner_phase": "Phase 47",
          "selector_source": "visible_controls:status,limit,offset,sort",
          "credentials": "omit",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const ROUTE_PATTERN = \"GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}\"",
            "function selectedRoute(selectors)",
            "async function loadAggregateTaskList()",
            "fetch(route"
          ]
        },
        {
          "name": "aggregate-search-visible-controls",
          "pattern": "GET /v1/tasks?field={task_search_field}&op={task_search_operator}&q={task_search_query}&status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "127.3",
          "owner_phase": "Phase 48",
          "selector_source": "visible_controls:field,op,q,status,limit,offset,sort",
          "credentials": "omit",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const SEARCH_FETCH_ROUTE_PATTERN = \"GET /v1/tasks?field={task_search_field}&op={task_search_operator}&q={task_search_query}&status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}\"",
            "function selectedSearchRoute(selectors)",
            "async function loadSearchTaskList()",
            "fetch(route"
          ]
        },
        {
          "name": "aggregate-explicit-bounded-search-traversal",
          "pattern": "GET /v1/tasks?field={task_search_field}&op={task_search_operator}&q={task_search_query}&status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "127.4",
          "owner_phase": "Phase 48",
          "selector_source": "visible_controls:field,op,q,status,limit,offset,sort,traversal_budget,traversal_rate,enable,cancel",
          "credentials": "omit",
          "headers": [],
          "request_body": "none",
          "trigger": "explicit traversal enable after healthy search response",
          "fetch_argument": "route",
          "notes": "same fetch call and route family as search; bounded 1..5 pages, one page per completed response, cancellable",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const TRAVERSAL_ENABLE_CONTROL_ID = \"aggregate-task-list-traversal-enable\"",
            "async function loadBoundedTraversal()",
            "selectedSearchRoute(nextSelectors)",
            "traversalEnableButton.addEventListener(\"click\", loadBoundedTraversal)"
          ]
        }
      ],
      "visible_control_ids": [
        "aggregate-task-list-status-control",
        "aggregate-task-list-limit-control",
        "aggregate-task-list-offset-control",
        "aggregate-task-list-load",
        "aggregate-task-list-previous-offset",
        "aggregate-task-list-next-offset",
        "aggregate-task-list-sort-control",
        "aggregate-task-list-search-field-control",
        "aggregate-task-list-search-op-control",
        "aggregate-task-list-search-query-control",
        "aggregate-task-list-search-load",
        "aggregate-task-list-traversal-budget-control",
        "aggregate-task-list-traversal-rate-control",
        "aggregate-task-list-traversal-enable",
        "aggregate-task-list-traversal-cancel"
      ],
      "metadata_target_ids": [
        "aggregate-task-list-status",
        "aggregate-task-list-source",
        "aggregate-task-list-traversal-state",
        "aggregate-task-list-selected-search-field",
        "aggregate-task-list-selected-search-op",
        "aggregate-task-list-selected-search-query",
        "aggregate-task-list-redaction",
        "aggregate-task-list-selected-status",
        "aggregate-task-list-selected-limit",
        "aggregate-task-list-selected-offset",
        "aggregate-task-list-selected-sort",
        "aggregate-task-list-freshness",
        "aggregate-task-list-authority",
        "aggregate-task-list-provenance",
        "aggregate-task-list-correlation",
        "aggregate-task-list-pagination",
        "aggregate-task-list-degraded",
        "aggregate-task-list-count",
        "aggregate-task-list-rows"
      ],
      "visible_source_ids": [],
      "side_channel_policy": {
        "classification": "forbidden_absent_except_explicit_story_127_4_traversal",
        "forbidden": [
          "EventSource",
          "WebSocket",
          "XMLHttpRequest",
          "localStorage",
          "sessionStorage",
          "document.cookie",
          "location.hash",
          "Worker",
          "serviceWorker",
          "setTimeout",
          "setInterval",
          "new Worker",
          "SharedWorker"
        ],
        "allowed": [
          "DOMContentLoaded wiring",
          "click/change handlers on visible controls",
          "Story 127.4 explicit traversal loop only after visible enable"
        ]
      },
      "dom_surfaces": [
        {
          "id": "aggregate-task-list-status-control",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-limit-control",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-offset-control",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-load",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-previous-offset",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-next-offset",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-sort-control",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-search-field-control",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-search-op-control",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-search-query-control",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-search-load",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-traversal-budget-control",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-traversal-rate-control",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-traversal-enable",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-traversal-cancel",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-source",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-traversal-state",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-selected-search-field",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-selected-search-op",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-selected-search-query",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-redaction",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-selected-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-selected-limit",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-selected-offset",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-selected-sort",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-freshness",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-authority",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-provenance",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-correlation",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-pagination",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-degraded",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-count",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        },
        {
          "id": "aggregate-task-list-rows",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "127.4",
          "owner_phase": "Phase 48"
        }
      ],
      "passive_global_sources": []
    },
    {
      "script": "session-list.js",
      "runtime_path": "dashboard/static/session-list.js",
      "classification": "live_guarded",
      "owner_story": "110.2",
      "owner_phase": "Phase 31",
      "routes": [
        {
          "name": "session-list",
          "pattern": "GET /v1/sessions",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "110.2",
          "owner_phase": "Phase 31",
          "selector_source": "none",
          "credentials": "default",
          "headers": [
            "Accept: application/json"
          ],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "ROUTE",
          "notes": "uses AbortSignal.timeout when available",
          "uses_signal": true,
          "body_argument_present": false,
          "source_markers": [
            "const ROUTE = \"/v1/sessions\"",
            "const ROUTE_PATTERN = \"GET /v1/sessions\"",
            "fetch(ROUTE"
          ]
        }
      ],
      "visible_control_ids": [],
      "metadata_target_ids": [
        "session-list-status",
        "session-list-source",
        "session-list-freshness",
        "session-list-authority",
        "session-list-provenance",
        "session-list-correlation",
        "session-list-pagination",
        "session-list-degraded",
        "session-list-count",
        "session-list-rows"
      ],
      "visible_source_ids": [],
      "side_channel_policy": {
        "classification": "forbidden_absent_with_bounded_abort_signal",
        "forbidden": [
          "EventSource",
          "WebSocket",
          "XMLHttpRequest",
          "localStorage",
          "sessionStorage",
          "document.cookie",
          "location.hash",
          "Worker",
          "serviceWorker",
          "setInterval",
          "new Worker",
          "SharedWorker"
        ],
        "allowed": [
          "DOMContentLoaded startup",
          "AbortSignal.timeout(8000) if browser supports it"
        ]
      },
      "dom_surfaces": [
        {
          "id": "session-list-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "110.2",
          "owner_phase": "Phase 31"
        },
        {
          "id": "session-list-source",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "110.2",
          "owner_phase": "Phase 31"
        },
        {
          "id": "session-list-freshness",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "110.2",
          "owner_phase": "Phase 31"
        },
        {
          "id": "session-list-authority",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "110.2",
          "owner_phase": "Phase 31"
        },
        {
          "id": "session-list-provenance",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "110.2",
          "owner_phase": "Phase 31"
        },
        {
          "id": "session-list-correlation",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "110.2",
          "owner_phase": "Phase 31"
        },
        {
          "id": "session-list-pagination",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "110.2",
          "owner_phase": "Phase 31"
        },
        {
          "id": "session-list-degraded",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "110.2",
          "owner_phase": "Phase 31"
        },
        {
          "id": "session-list-count",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "110.2",
          "owner_phase": "Phase 31"
        },
        {
          "id": "session-list-rows",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "110.2",
          "owner_phase": "Phase 31"
        }
      ],
      "passive_global_sources": []
    },
    {
      "script": "session-detail.js",
      "runtime_path": "dashboard/static/session-detail.js",
      "classification": "live_guarded",
      "owner_story": "111.2",
      "owner_phase": "Phase 32",
      "routes": [
        {
          "name": "session-detail",
          "pattern": "GET /v1/sessions/{session_id}",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "111.2",
          "owner_phase": "Phase 32",
          "selector_source": "visible_source:session-detail-session-id-source",
          "credentials": "default",
          "headers": [
            "Accept: application/json"
          ],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const ROUTE_PATTERN = \"GET /v1/sessions/{session_id}\"",
            "const ROUTE_PREFIX = \"/v1/sessions/\"",
            "fetch(route"
          ]
        }
      ],
      "visible_control_ids": [],
      "metadata_target_ids": [
        "session-detail-status",
        "session-detail-source",
        "session-detail-freshness",
        "session-detail-authority",
        "session-detail-provenance",
        "session-detail-correlation",
        "session-detail-degraded",
        "session-detail-row"
      ],
      "visible_source_ids": [
        "session-detail-session-id-source"
      ],
      "side_channel_policy": {
        "classification": "forbidden_absent",
        "forbidden": [
          "EventSource",
          "WebSocket",
          "XMLHttpRequest",
          "localStorage",
          "sessionStorage",
          "document.cookie",
          "location.hash",
          "Worker",
          "serviceWorker",
          "new Worker",
          "SharedWorker"
        ],
        "allowed": [
          "DOMContentLoaded explicit startup only"
        ]
      },
      "dom_surfaces": [
        {
          "id": "session-detail-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "111.2",
          "owner_phase": "Phase 32"
        },
        {
          "id": "session-detail-source",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "111.2",
          "owner_phase": "Phase 32"
        },
        {
          "id": "session-detail-freshness",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "111.2",
          "owner_phase": "Phase 32"
        },
        {
          "id": "session-detail-authority",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "111.2",
          "owner_phase": "Phase 32"
        },
        {
          "id": "session-detail-provenance",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "111.2",
          "owner_phase": "Phase 32"
        },
        {
          "id": "session-detail-correlation",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "111.2",
          "owner_phase": "Phase 32"
        },
        {
          "id": "session-detail-degraded",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "111.2",
          "owner_phase": "Phase 32"
        },
        {
          "id": "session-detail-row",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "111.2",
          "owner_phase": "Phase 32"
        },
        {
          "id": "session-detail-session-id-source",
          "role": "visible_source",
          "classification": "live_visible_source",
          "owner_story": "111.2",
          "owner_phase": "Phase 32"
        }
      ],
      "passive_global_sources": []
    },
    {
      "script": "event-timeline.js",
      "runtime_path": "dashboard/static/event-timeline.js",
      "classification": "live_guarded",
      "owner_story": "103.2",
      "owner_phase": "Phase 24",
      "routes": [
        {
          "name": "task-events",
          "pattern": "GET /v1/tasks/{task_id}/events",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "103.2",
          "owner_phase": "Phase 24",
          "selector_source": "visible_source:event-timeline-task-id-source",
          "credentials": "default",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const EVENTS_PATTERN = \"GET /v1/tasks/{task_id}/events\"",
            "const ROUTE_PREFIX = \"/v1/tasks/\"",
            "const eventsRoute = ROUTE_PREFIX + encodedTaskId + EVENTS_SUFFIX",
            "readRoute(eventsRoute, \"events\", taskId)"
          ]
        },
        {
          "name": "task-transitions",
          "pattern": "GET /v1/tasks/{task_id}/transitions",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "103.2",
          "owner_phase": "Phase 24",
          "selector_source": "visible_source:event-timeline-task-id-source",
          "credentials": "default",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const TRANSITIONS_PATTERN = \"GET /v1/tasks/{task_id}/transitions\"",
            "const ROUTE_PREFIX = \"/v1/tasks/\"",
            "const transitionsRoute = ROUTE_PREFIX + encodedTaskId + TRANSITIONS_SUFFIX",
            "readRoute(transitionsRoute, \"transitions\", taskId)"
          ]
        }
      ],
      "visible_control_ids": [],
      "metadata_target_ids": [
        "event-timeline-status",
        "event-timeline-source",
        "event-timeline-task-id",
        "event-timeline-freshness",
        "event-timeline-authority",
        "event-timeline-event-count",
        "event-timeline-transition-count",
        "event-timeline-detail"
      ],
      "visible_source_ids": [
        "event-timeline-task-id-source"
      ],
      "side_channel_policy": {
        "classification": "forbidden_absent",
        "forbidden": [
          "EventSource",
          "WebSocket",
          "XMLHttpRequest",
          "localStorage",
          "sessionStorage",
          "document.cookie",
          "location.hash",
          "Worker",
          "serviceWorker",
          "new Worker",
          "SharedWorker"
        ],
        "allowed": [
          "DOMContentLoaded explicit startup only"
        ]
      },
      "dom_surfaces": [
        {
          "id": "event-timeline-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "103.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "event-timeline-source",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "103.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "event-timeline-task-id",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "103.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "event-timeline-freshness",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "103.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "event-timeline-authority",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "103.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "event-timeline-event-count",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "103.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "event-timeline-transition-count",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "103.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "event-timeline-detail",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "103.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "event-timeline-task-id-source",
          "role": "visible_source",
          "classification": "live_visible_source",
          "owner_story": "103.2",
          "owner_phase": "Phase 24"
        }
      ],
      "passive_global_sources": []
    },
    {
      "script": "trace-correlation.js",
      "runtime_path": "dashboard/static/trace-correlation.js",
      "classification": "live_guarded",
      "owner_story": "104.2",
      "owner_phase": "Phase 24",
      "routes": [
        {
          "name": "trace-correlation",
          "pattern": "GET /v1/trace/{trace_id}",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "104.2",
          "owner_phase": "Phase 24",
          "selector_source": "visible_source:trace-correlation-trace-id-source",
          "credentials": "default",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const TRACE_PATTERN = \"GET /v1/trace/{trace_id}\"",
            "const ROUTE_PREFIX = \"/v1/trace/\"",
            "fetch(route"
          ]
        }
      ],
      "visible_control_ids": [],
      "metadata_target_ids": [
        "trace-correlation-status",
        "trace-correlation-source",
        "trace-correlation-trace-id",
        "trace-correlation-freshness",
        "trace-correlation-authority",
        "trace-correlation-row-count",
        "trace-correlation-linked-identifiers",
        "trace-correlation-detail"
      ],
      "visible_source_ids": [
        "trace-correlation-trace-id-source"
      ],
      "side_channel_policy": {
        "classification": "forbidden_absent",
        "forbidden": [
          "EventSource",
          "WebSocket",
          "XMLHttpRequest",
          "localStorage",
          "sessionStorage",
          "document.cookie",
          "location.hash",
          "Worker",
          "serviceWorker",
          "new Worker",
          "SharedWorker"
        ],
        "allowed": [
          "DOMContentLoaded explicit startup only"
        ]
      },
      "dom_surfaces": [
        {
          "id": "trace-correlation-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "104.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "trace-correlation-source",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "104.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "trace-correlation-trace-id",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "104.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "trace-correlation-freshness",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "104.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "trace-correlation-authority",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "104.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "trace-correlation-row-count",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "104.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "trace-correlation-linked-identifiers",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "104.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "trace-correlation-detail",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "104.2",
          "owner_phase": "Phase 24"
        },
        {
          "id": "trace-correlation-trace-id-source",
          "role": "visible_source",
          "classification": "live_visible_source",
          "owner_story": "104.2",
          "owner_phase": "Phase 24"
        }
      ],
      "passive_global_sources": []
    },
    {
      "script": "history-replay.js",
      "runtime_path": "dashboard/static/history-replay.js",
      "classification": "live_guarded",
      "owner_story": "105.2",
      "owner_phase": "Phase 26",
      "routes": [
        {
          "name": "task-history",
          "pattern": "GET /v1/tasks/{task_id}/history",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "105.2",
          "owner_phase": "Phase 26",
          "selector_source": "visible_source:history-replay-task-id-source",
          "credentials": "default",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const HISTORY_PATTERN = \"GET /v1/tasks/{task_id}/history\"",
            "const ROUTE_SUFFIX = \"/history\"",
            "read(route, task, \"history\")"
          ]
        },
        {
          "name": "replay-readiness",
          "pattern": "GET /v1/events/replay",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "105.2",
          "owner_phase": "Phase 26",
          "selector_source": "visible_sources:history-replay-target-kind-source,history-replay-target-value-source",
          "credentials": "default",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const REPLAY_PATTERN = \"GET /v1/events/replay\"",
            "const REPLAY_ROUTE = \"/v1/events/replay\"",
            "read(replay, task, \"replay\")"
          ]
        },
        {
          "name": "replay-validation",
          "pattern": "GET /v1/events/replay/validate",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "105.2",
          "owner_phase": "Phase 26",
          "selector_source": "none",
          "credentials": "default",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const VALIDATE_PATTERN = \"GET /v1/events/replay/validate\"",
            "const VALIDATE_ROUTE = \"/v1/events/replay/validate\"",
            "read(VALIDATE_ROUTE, task, \"validation\")"
          ]
        }
      ],
      "visible_control_ids": [],
      "metadata_target_ids": [
        "history-replay-status",
        "history-replay-source",
        "history-replay-task-id",
        "history-replay-target",
        "history-replay-freshness",
        "history-replay-authority",
        "history-replay-history-count",
        "history-replay-replay-count",
        "history-replay-validation-status",
        "history-replay-linked-identifiers",
        "history-replay-detail"
      ],
      "visible_source_ids": [
        "history-replay-task-id-source",
        "history-replay-target-kind-source",
        "history-replay-target-value-source"
      ],
      "side_channel_policy": {
        "classification": "forbidden_absent",
        "forbidden": [
          "EventSource",
          "WebSocket",
          "XMLHttpRequest",
          "localStorage",
          "sessionStorage",
          "document.cookie",
          "location.hash",
          "Worker",
          "serviceWorker",
          "new Worker",
          "SharedWorker"
        ],
        "allowed": [
          "DOMContentLoaded explicit startup only"
        ]
      },
      "dom_surfaces": [
        {
          "id": "history-replay-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-source",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-task-id",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-target",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-freshness",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-authority",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-history-count",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-replay-count",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-validation-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-linked-identifiers",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-detail",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-task-id-source",
          "role": "visible_source",
          "classification": "live_visible_source",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-target-kind-source",
          "role": "visible_source",
          "classification": "live_visible_source",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        },
        {
          "id": "history-replay-target-value-source",
          "role": "visible_source",
          "classification": "live_visible_source",
          "owner_story": "105.2",
          "owner_phase": "Phase 26"
        }
      ],
      "passive_global_sources": []
    },
    {
      "script": "lifecycle-snapshot.js",
      "runtime_path": "dashboard/static/lifecycle-snapshot.js",
      "classification": "live_guarded",
      "owner_story": "107.2",
      "owner_phase": "Phase 28",
      "routes": [
        {
          "name": "snapshot-list",
          "pattern": "GET /v1/events/replay/snapshots",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "107.2",
          "owner_phase": "Phase 28",
          "selector_source": "none",
          "credentials": "default",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "ROUTE",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const ROUTE = \"/v1/events/replay/snapshots\"",
            "fetch(ROUTE"
          ]
        },
        {
          "name": "snapshot-create-visible-bearer-token",
          "pattern": "POST /v1/events/replay/snapshots",
          "method": "POST",
          "classification": "live_visible_bearer_token_create_affordance",
          "owner_story": "107.2",
          "owner_phase": "Phase 28",
          "selector_source": "visible_control:lifecycle-snapshot-create-token",
          "credentials": "default",
          "headers": [
            "Authorization: visible bearer token"
          ],
          "request_body": "none",
          "trigger": "explicit click:lifecycle-snapshot-create-button",
          "fetch_argument": "CREATE_ROUTE",
          "notes": "browser checks only Bearer prefix; backend owns token authorization semantics",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const CREATE_ROUTE = \"/v1/events/replay/snapshots\"",
            "token.startsWith(\"Bearer \")",
            "fetch(CREATE_ROUTE",
            "authorization source=existing bearer token"
          ]
        }
      ],
      "visible_control_ids": [
        "lifecycle-snapshot-create-token",
        "lifecycle-snapshot-create-button"
      ],
      "metadata_target_ids": [
        "lifecycle-snapshot-status",
        "lifecycle-snapshot-source",
        "lifecycle-snapshot-count",
        "lifecycle-snapshot-freshness",
        "lifecycle-snapshot-authority",
        "lifecycle-snapshot-items",
        "lifecycle-snapshot-evidence",
        "lifecycle-snapshot-degraded",
        "lifecycle-snapshot-detail",
        "lifecycle-snapshot-create-status",
        "lifecycle-snapshot-create-result"
      ],
      "visible_source_ids": [],
      "side_channel_policy": {
        "classification": "forbidden_absent_except_visible_snapshot_create",
        "forbidden": [
          "EventSource",
          "WebSocket",
          "XMLHttpRequest",
          "localStorage",
          "sessionStorage",
          "document.cookie",
          "location.hash",
          "Worker",
          "serviceWorker",
          "setTimeout",
          "setInterval",
          "new Worker",
          "SharedWorker"
        ],
        "allowed": [
          "DOMContentLoaded startup",
          "explicit click handler for visible snapshot create"
        ]
      },
      "dom_surfaces": [
        {
          "id": "lifecycle-snapshot-create-token",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-create-button",
          "role": "visible_control",
          "classification": "live_visible_control",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-source",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-count",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-freshness",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-authority",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-items",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-evidence",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-degraded",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-detail",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-create-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        },
        {
          "id": "lifecycle-snapshot-create-result",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "107.2",
          "owner_phase": "Phase 28"
        }
      ],
      "passive_global_sources": [
        {
          "name": "window.LIFECYCLE_SNAPSHOT_EVIDENCE",
          "alternate_name": "LIFECYCLE_SNAPSHOT_EVIDENCE",
          "classification": "live_passive_global_evidence_source",
          "owner_story": "107.2",
          "owner_phase": "Phase 28",
          "policy": "optional passive lifecycle evidence object; absence renders non-authoritative degraded state and does not trigger adjacent fetches"
        }
      ]
    },
    {
      "script": "task-log-digest.js",
      "runtime_path": "dashboard/static/task-log-digest.js",
      "classification": "live_guarded",
      "owner_story": "108.2",
      "owner_phase": "Phase 29",
      "routes": [
        {
          "name": "task-log-digest",
          "pattern": "GET /v1/tasks/{task_id}/logs/digest",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "108.2",
          "owner_phase": "Phase 29",
          "selector_source": "visible_source:task-log-digest-task-id-source",
          "credentials": "default",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "",
          "uses_signal": false,
          "body_argument_present": false,
          "source_markers": [
            "const ROUTE_PATTERN = \"GET /v1/tasks/{task_id}/logs/digest\"",
            "const ROUTE_SUFFIX = \"/logs/digest\"",
            "fetch(route"
          ]
        }
      ],
      "visible_control_ids": [],
      "metadata_target_ids": [
        "task-log-digest-status",
        "task-log-digest-source",
        "task-log-digest-task-id",
        "task-log-digest-freshness",
        "task-log-digest-authority",
        "task-log-digest-provenance",
        "task-log-digest-correlation",
        "task-log-digest-degraded",
        "task-log-digest-detail"
      ],
      "visible_source_ids": [
        "task-log-digest-task-id-source"
      ],
      "side_channel_policy": {
        "classification": "forbidden_absent",
        "forbidden": [
          "EventSource",
          "WebSocket",
          "XMLHttpRequest",
          "localStorage",
          "sessionStorage",
          "document.cookie",
          "location.hash",
          "Worker",
          "serviceWorker",
          "new Worker",
          "SharedWorker"
        ],
        "allowed": [
          "DOMContentLoaded explicit startup only"
        ]
      },
      "dom_surfaces": [
        {
          "id": "task-log-digest-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "108.2",
          "owner_phase": "Phase 29"
        },
        {
          "id": "task-log-digest-source",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "108.2",
          "owner_phase": "Phase 29"
        },
        {
          "id": "task-log-digest-task-id",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "108.2",
          "owner_phase": "Phase 29"
        },
        {
          "id": "task-log-digest-freshness",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "108.2",
          "owner_phase": "Phase 29"
        },
        {
          "id": "task-log-digest-authority",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "108.2",
          "owner_phase": "Phase 29"
        },
        {
          "id": "task-log-digest-provenance",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "108.2",
          "owner_phase": "Phase 29"
        },
        {
          "id": "task-log-digest-correlation",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "108.2",
          "owner_phase": "Phase 29"
        },
        {
          "id": "task-log-digest-degraded",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "108.2",
          "owner_phase": "Phase 29"
        },
        {
          "id": "task-log-digest-detail",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "108.2",
          "owner_phase": "Phase 29"
        },
        {
          "id": "task-log-digest-task-id-source",
          "role": "visible_source",
          "classification": "live_visible_source",
          "owner_story": "108.2",
          "owner_phase": "Phase 29"
        }
      ],
      "passive_global_sources": []
    },
    {
      "script": "digest-stream.js",
      "runtime_path": "dashboard/static/digest-stream.js",
      "classification": "live_guarded",
      "owner_story": "112.2",
      "owner_phase": "Phase 33",
      "routes": [
        {
          "name": "digest-stream",
          "pattern": "GET /v1/tasks/{task_id}/logs/digest/stream",
          "method": "GET",
          "classification": "live_read",
          "owner_story": "112.2",
          "owner_phase": "Phase 33",
          "selector_source": "visible_source:digest-stream-task-id-source",
          "credentials": "default",
          "headers": [],
          "request_body": "none",
          "trigger": "DOMContentLoaded",
          "fetch_argument": "route",
          "notes": "uses fetch ReadableStream reader with AbortController and bounded setTimeout timeout; not EventSource/WebSocket/XMLHttpRequest",
          "uses_signal": true,
          "body_argument_present": false,
          "source_markers": [
            "const ROUTE_PATTERN = \"GET /v1/tasks/{task_id}/logs/digest/stream\"",
            "const ROUTE_SUFFIX = \"/logs/digest/stream\"",
            "fetch(route"
          ]
        }
      ],
      "visible_control_ids": [],
      "metadata_target_ids": [
        "digest-stream-status",
        "digest-stream-source",
        "digest-stream-task-id",
        "digest-stream-freshness",
        "digest-stream-authority",
        "digest-stream-provenance",
        "digest-stream-correlation",
        "digest-stream-degraded",
        "digest-stream-detail"
      ],
      "visible_source_ids": [
        "digest-stream-task-id-source"
      ],
      "side_channel_policy": {
        "classification": "bounded_fetch_stream_no_side_channel_transport",
        "forbidden": [
          "EventSource",
          "WebSocket",
          "XMLHttpRequest",
          "localStorage",
          "sessionStorage",
          "document.cookie",
          "location.hash",
          "Worker",
          "serviceWorker",
          "setInterval",
          "new Worker",
          "SharedWorker"
        ],
        "allowed": [
          "DOMContentLoaded startup",
          "ReadableStream reader from fetch response body",
          "AbortController",
          "setTimeout bounded stream timeout",
          "clearTimeout cleanup"
        ]
      },
      "dom_surfaces": [
        {
          "id": "digest-stream-status",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "112.2",
          "owner_phase": "Phase 33"
        },
        {
          "id": "digest-stream-source",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "112.2",
          "owner_phase": "Phase 33"
        },
        {
          "id": "digest-stream-task-id",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "112.2",
          "owner_phase": "Phase 33"
        },
        {
          "id": "digest-stream-freshness",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "112.2",
          "owner_phase": "Phase 33"
        },
        {
          "id": "digest-stream-authority",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "112.2",
          "owner_phase": "Phase 33"
        },
        {
          "id": "digest-stream-provenance",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "112.2",
          "owner_phase": "Phase 33"
        },
        {
          "id": "digest-stream-correlation",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "112.2",
          "owner_phase": "Phase 33"
        },
        {
          "id": "digest-stream-degraded",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "112.2",
          "owner_phase": "Phase 33"
        },
        {
          "id": "digest-stream-detail",
          "role": "metadata_target",
          "classification": "live_metadata_target",
          "owner_story": "112.2",
          "owner_phase": "Phase 33"
        },
        {
          "id": "digest-stream-task-id-source",
          "role": "visible_source",
          "classification": "live_visible_source",
          "owner_story": "112.2",
          "owner_phase": "Phase 33"
        }
      ],
      "passive_global_sources": []
    }
  ],
  "aggregate_task_list": {
    "runtime_path": "dashboard/static/aggregate-task-list.js",
    "classification": "live_guarded",
    "owner_story": "127.4",
    "owner_phase": "Phase 48",
    "approved_fetch_base": "/v1/tasks",
    "authorized_sort_values": [
      "updated_at_desc_id_asc",
      "created_at_desc_id_asc"
    ],
    "authorized_status_values": [
      "pending",
      "planning",
      "plan_ready",
      "executing",
      "blocked",
      "completed",
      "stopped",
      "failed"
    ],
    "authorized_search_fields": [
      "task_id",
      "title",
      "actor_id",
      "last_event_type",
      "updated_at",
      "created_at"
    ],
    "authorized_search_ops_by_field": {
      "task_id": [
        "eq"
      ],
      "title": [
        "contains",
        "prefix"
      ],
      "actor_id": [
        "eq",
        "prefix"
      ],
      "last_event_type": [
        "eq"
      ],
      "updated_at": [
        "gte",
        "lte"
      ],
      "created_at": [
        "gte",
        "lte"
      ]
    },
    "traversal_policy": "Story 127.4 only: explicit enable, visible budget 1..5, one page per completed response, cancellable, search-result-only, stale/non-authoritative/mismatched selectors fail closed"
  },
  "deferred_or_forbidden_surfaces": [
    {
      "name": "broad-dashboard-runtime-rewiring",
      "classification": "deferred",
      "owner_story": "128.2-128.7",
      "owner_phase": "Phase 48",
      "policy": "future cleanup slices only; Story 128.1 does not change runtime behavior"
    },
    {
      "name": "hidden-selectors",
      "classification": "forbidden",
      "owner_story": "128.1",
      "owner_phase": "Phase 48",
      "policy": "URL/hash/storage/cookie/row-derived/server-provided/background selectors remain fail-closed"
    },
    {
      "name": "automatic-traversal-side-channels",
      "classification": "forbidden_except_story_127_4_explicit_traversal",
      "owner_story": "127.4",
      "owner_phase": "Phase 48",
      "policy": "no prefetch, timer/worker/observer retry loop, cache warming, EventSource, WebSocket, or XMLHttpRequest"
    },
    {
      "name": "generated-live-data-or-browser-summary",
      "classification": "forbidden",
      "owner_story": "128.1",
      "owner_phase": "Phase 48",
      "policy": "dashboard renders backend metadata/text only; no browser-generated summaries or live data"
    },
    {
      "name": "epic-128-closure-or-shipped-status",
      "classification": "deferred",
      "owner_story": "128.7",
      "owner_phase": "Phase 48",
      "policy": "Story 128.1 may mark only local inventory/test-guard completion; Epic 128 remains in-progress and remote CI/shipped evidence pending"
    }
  ],
  "broad_cleanup": {
    "runtime_rewiring_authorized": false,
    "classification": "deferred",
    "owner_story": "128.2-128.7",
    "owner_phase": "Phase 48",
    "future_cleanup_policy": "future stories must be narrow file-level behavior-preserving slices guarded by this inventory before edits"
  }
}
```

## Live vs deferred classification

- Live guarded: static shell script order and the eleven dashboard runtime modules listed in the inventory.
- Live visible mutation-adjacent affordance: only the existing lifecycle snapshot create POST, from visible bearer-token input and explicit button click; browser checks only the `Bearer ` prefix and backend owns token authorization semantics.
- Deferred: Epic 128 runtime cleanup/helper extraction and Epic 128 closure until later stories.
- Forbidden: hidden selectors, row-derived selectors, URL/hash/storage/cookie selectors, generated live data, side-channel transports, and automatic traversal outside Story 127.4 explicit bounded search traversal.

## Test guards

`tests/dashboard/test_dashboard_wiring_inventory.py` parses this JSON and checks bidirectional drift: static shell scripts, module files, per-DOM ownership/classification, passive global sources, route patterns, exact source markers, fetch signatures, forbidden side-channel markers, and local-only status policy.
