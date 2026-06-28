(function () {
  "use strict";

  const ROUTE = "/v1/tasks";
  const ROUTE_PATTERN = "GET /v1/tasks?status={task_status}&limit={task_list_limit}";
  const MAX_LIMIT = 50;
  const STATUS_CONTROL_ID = "aggregate-task-list-status-control";
  const LIMIT_CONTROL_ID = "aggregate-task-list-limit-control";
  const LOAD_CONTROL_ID = "aggregate-task-list-load";
  const ALLOWED_STATUSES = new Set(["pending", "planning", "plan_ready", "executing", "blocked", "completed", "stopped", "failed"]);
  const ROW_KEYS = ["task_id", "status", "title", "created_at", "updated_at", "state_since", "actor", "last_event"];
  const ACTOR_KEYS = ["kind", "id"];
  const EVENT_KEYS = ["id", "type", "emitted_at", "trace_id"];
  const BODY_KEYS = ["route", "selected_status", "selected_limit", "retrieved_at", "freshness_state", "display_state", "authority_state", "provenance", "request_id", "trace_id", "correlation_id", "limit", "returned_count", "has_more", "next_offset", "items"];
  const ALLOWED_DISPLAY_STATES = new Set(["healthy", "empty-list", "stale", "invalid", "unauthorized", "backend-unavailable", "unavailable"]);
  const ALLOWED_FRESHNESS_STATES = new Set(["fresh", "stale"]);

  function element(id) {
    return document.getElementById(id);
  }

  function write(id, value) {
    const target = element(id);
    if (target) target.textContent = value;
  }

  function label(value, fallback) {
    if (typeof value !== "string") return fallback;
    const text = value.trim();
    return text || fallback;
  }

  function sameKeys(value, allowed) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const keys = Object.keys(value).sort();
    const expected = allowed.slice().sort();
    return keys.length === expected.length && keys.every((key, index) => key === expected[index]);
  }

  function validActor(actor) {
    return sameKeys(actor, ACTOR_KEYS) && typeof actor.kind === "string" && typeof actor.id === "string";
  }

  function validLastEvent(lastEvent) {
    return lastEvent === null || (sameKeys(lastEvent, EVENT_KEYS) && typeof lastEvent.id === "string" && typeof lastEvent.type === "string" && typeof lastEvent.emitted_at === "string" && (lastEvent.trace_id === null || typeof lastEvent.trace_id === "string"));
  }

  function validRow(row, selectors) {
    return Boolean(
      sameKeys(row, ROW_KEYS) &&
        typeof row.task_id === "string" &&
        row.status === selectors.status &&
        (row.title === null || typeof row.title === "string") &&
        typeof row.created_at === "string" &&
        typeof row.updated_at === "string" &&
        typeof row.state_since === "string" &&
        validActor(row.actor) &&
        validLastEvent(row.last_event)
    );
  }

  function readSelectors() {
    const statusControl = element(STATUS_CONTROL_ID);
    const limitControl = element(LIMIT_CONTROL_ID);
    if (!statusControl || !limitControl) return null;
    if (statusControl.type === "hidden" || limitControl.type === "hidden") return null;
    const status = label(statusControl.value, "");
    const limitText = label(limitControl.value, "");
    if (!ALLOWED_STATUSES.has(status)) return null;
    if (!/^[1-9][0-9]?$/.test(limitText)) return null;
    const limit = Number(limitText);
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) return null;
    return { status, limit: String(limit) };
  }

  function validMetadata(body, selectors) {
    if (!body || typeof body !== "object" || Array.isArray(body)) return false;
    if (!sameKeys(body, BODY_KEYS)) return false;
    if (body.route !== ROUTE_PATTERN) return false;
    if (body.selected_status !== selectors.status) return false;
    if (body.selected_limit !== Number(selectors.limit)) return false;
    if (!ALLOWED_FRESHNESS_STATES.has(label(body.freshness_state, ""))) return false;
    if (!ALLOWED_DISPLAY_STATES.has(label(body.display_state, ""))) return false;
    if (body.authority_state !== "authoritative" && body.authority_state !== "non-authoritative") return false;
    if (body.display_state === "healthy" && body.authority_state !== "authoritative") return false;
    if (body.display_state !== "healthy" && body.authority_state === "authoritative") return false;
    if (typeof body.retrieved_at !== "string" || !body.retrieved_at) return false;
    if (typeof body.provenance !== "string" || !body.provenance) return false;
    if (typeof body.request_id !== "string" || !body.request_id) return false;
    if (body.trace_id !== null && typeof body.trace_id !== "string") return false;
    if (typeof body.correlation_id !== "string" || !body.correlation_id) return false;
    if (body.limit !== Number(selectors.limit)) return false;
    if (typeof body.returned_count !== "number" || body.returned_count < 0) return false;
    if (typeof body.has_more !== "boolean") return false;
    if (body.next_offset !== null) return false;
    if (!Array.isArray(body.items) || body.items.length > Number(selectors.limit)) return false;
    if (body.items.length !== body.returned_count) return false;
    return body.items.every((row) => validRow(row, selectors));
  }

  function selectedRoute(selectors) {
    return `${ROUTE}?status=${selectors.status}&limit=${selectors.limit}`;
  }

  function authorityFor(state) {
    return state === "healthy" ? "authoritative" : "non-authoritative";
  }

  function render(state, authority, freshness, provenance, correlation, selectedStatus, selectedLimit, runtimeRoute, pagination, degraded, count, rows) {
    write("aggregate-task-list-status", `Aggregate task list state: ${state}.`);
    write("aggregate-task-list-source", `Source: ${ROUTE_PATTERN}. Runtime route: ${runtimeRoute}.`);
    write("aggregate-task-list-selected-status", `Selected status: ${selectedStatus}.`);
    write("aggregate-task-list-selected-limit", `Selected limit: ${selectedLimit}.`);
    write("aggregate-task-list-freshness", `Freshness: ${freshness}.`);
    write("aggregate-task-list-authority", `Authority: ${authority}.`);
    write("aggregate-task-list-provenance", `Provenance: ${provenance}.`);
    write("aggregate-task-list-correlation", `Correlation: ${correlation}.`);
    write("aggregate-task-list-pagination", `Pagination: ${pagination}.`);
    write("aggregate-task-list-degraded", `Degraded state: ${degraded}.`);
    write("aggregate-task-list-count", `Task rows: ${count}.`);
    write("aggregate-task-list-rows", rows);
  }

  function renderClosed(state, detail, selectors) {
    const selectedStatus = selectors ? selectors.status : "unavailable";
    const selectedLimit = selectors ? selectors.limit : "unavailable";
    const runtimeRoute = selectors ? selectedRoute(selectors) : ROUTE_PATTERN;
    render(state, "non-authoritative", "missing server freshness", "backend task summary list", "not provided", selectedStatus, selectedLimit, runtimeRoute, "fixed first page unavailable", state, "0", detail);
  }

  function rowText(row) {
    const event = row.last_event;
    const eventText = event ? `last_event ${event.id} ${event.type} ${event.emitted_at} trace ${label(event.trace_id, "not provided")}` : "last_event none";
    return `${row.task_id} ${row.status} ${label(row.title, "untitled")} created ${row.created_at} updated ${row.updated_at} state_since ${row.state_since} actor ${row.actor.kind}/${row.actor.id} ${eventText}`;
  }

  function renderBody(body, selectors) {
    if (!validMetadata(body, selectors)) {
      renderClosed("invalid", "invalid aggregate task list status and limit response; not authoritative.", selectors);
      return;
    }
    const state = body.display_state;
    const authority = authorityFor(state);
    const correlation = label(body.correlation_id, label(body.request_id, label(body.trace_id, "not provided")));
    const provenance = label(body.provenance, "backend task summary list");
    const pagination = `selected status ${body.selected_status}; selected limit ${body.selected_limit}; returned ${body.returned_count}; has_more ${body.has_more}; next_offset none`;
    const runtimeRoute = selectedRoute(selectors);
    if (state === "empty-list") {
      render(state, "non-authoritative", body.retrieved_at, provenance, correlation, body.selected_status, String(body.selected_limit), runtimeRoute, pagination, state, "0", "empty successful read; no task rows returned.");
      return;
    }
    if (state !== "healthy") {
      render(state, "non-authoritative", body.retrieved_at, provenance, correlation, body.selected_status, String(body.selected_limit), runtimeRoute, pagination, state, String(body.returned_count), `${state} aggregate task list status and limit response; not authoritative.`);
      return;
    }
    render(state, authority, body.retrieved_at, provenance, correlation, body.selected_status, String(body.selected_limit), runtimeRoute, pagination, "none", String(body.returned_count), body.items.map(rowText).join("\n"));
  }

  async function loadAggregateTaskList() {
    const selectors = readSelectors();
    if (!selectors) {
      renderClosed("invalid", "invalid visible aggregate task-list status or limit selector; not authoritative.", null);
      return;
    }
    const route = selectedRoute(selectors);
    try {
      const response = await fetch(route, { method: "GET", credentials: "omit" });
      if (!response.ok) {
        const state = response.status === 401 || response.status === 403 ? "unauthorized" : "backend-unavailable";
        renderClosed(state, `${state.replace(/-/g, " ")} response for aggregate task list status and limit read; not authoritative.`, selectors);
        return;
      }
      let body;
      try {
        body = await response.json();
      } catch (_error) {
        renderClosed("invalid", "invalid aggregate task list status and limit response; not authoritative.", selectors);
        return;
      }
      renderBody(body, selectors);
    } catch (_error) {
      renderClosed("backend-unavailable", "backend unavailable for aggregate task list status and limit read; not authoritative.", selectors);
    }
  }

  function startAggregateTaskList() {
    const loadButton = element(LOAD_CONTROL_ID);
    if (loadButton && typeof loadButton.addEventListener === "function") {
      loadButton.addEventListener("click", loadAggregateTaskList);
    }
    return loadAggregateTaskList();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startAggregateTaskList);
  } else {
    const pending = startAggregateTaskList();
    if (typeof window !== "undefined") window.__aggregateTaskListReady = pending;
  }
})();
