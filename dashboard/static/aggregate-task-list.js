(function () {
  "use strict";

  const ROUTE = "/v1/tasks";
  const ROUTE_PATTERN = "GET /v1/tasks?status={task_status}&limit={task_list_limit}&offset={task_list_offset}&sort={task_sort}";
  const SORT_VALUES = new Set(["updated_at_desc_id_asc", "created_at_desc_id_asc"]);
  const MAX_LIMIT = 50;
  const MAX_OFFSET = 2147483647;
  const STATUS_CONTROL_ID = "aggregate-task-list-status-control";
  const LIMIT_CONTROL_ID = "aggregate-task-list-limit-control";
  const OFFSET_CONTROL_ID = "aggregate-task-list-offset-control";
  const SORT_CONTROL_ID = "aggregate-task-list-sort-control";
  const LOAD_CONTROL_ID = "aggregate-task-list-load";
  const PREVIOUS_CONTROL_ID = "aggregate-task-list-previous-offset";
  const NEXT_CONTROL_ID = "aggregate-task-list-next-offset";
  const ALLOWED_ROW_STATUSES = new Set(["pending", "planning", "plan_ready", "executing", "blocked", "completed", "stopped", "failed"]);
  const ROW_KEYS = ["task_id", "status", "title", "created_at", "updated_at", "state_since", "actor", "last_event"];
  const ACTOR_KEYS = ["kind", "id"];
  const EVENT_KEYS = ["id", "type", "emitted_at", "trace_id"];
  const BODY_KEYS = ["route", "selected_status", "selected_limit", "selected_offset", "selected_sort", "retrieved_at", "freshness_state", "display_state", "authority_state", "provenance", "request_id", "trace_id", "correlation_id", "limit", "returned_count", "has_more", "next_offset", "items"];
  const ALLOWED_DISPLAY_STATES = new Set(["healthy", "empty-list", "stale", "invalid", "unauthorized", "backend-unavailable", "unavailable"]);
  const ALLOWED_FRESHNESS_STATES = new Set(["fresh", "stale"]);
  const navigationState = { status: null, limit: null, offset: null, sort: null, previousOffset: null, nextOffset: null };
  let loadInFlight = false;
  let selectorEditInvalidated = false;

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

  function validRow(row, selectedStatus) {
    return Boolean(
      sameKeys(row, ROW_KEYS) &&
        typeof row.task_id === "string" &&
        ALLOWED_ROW_STATUSES.has(row.status) &&
        row.status === selectedStatus &&
        (row.title === null || typeof row.title === "string") &&
        typeof row.created_at === "string" &&
        typeof row.updated_at === "string" &&
        typeof row.state_since === "string" &&
        validActor(row.actor) &&
        validLastEvent(row.last_event)
    );
  }

  function decimalText(value, pattern) {
    const text = label(value, "");
    return pattern.test(text) ? text : null;
  }

  function readSelectors() {
    const statusControl = element(STATUS_CONTROL_ID);
    const limitControl = element(LIMIT_CONTROL_ID);
    const offsetControl = element(OFFSET_CONTROL_ID);
    const sortControl = element(SORT_CONTROL_ID);
    if (!statusControl || !limitControl || !offsetControl || !sortControl) return null;
    if (statusControl.type === "hidden" || limitControl.type === "hidden" || offsetControl.type === "hidden" || sortControl.type === "hidden") return null;
    const status = label(statusControl.value, "");
    if (!ALLOWED_ROW_STATUSES.has(status)) return null;
    const sort = label(sortControl.value, "");
    if (!SORT_VALUES.has(sort)) return null;
    const limitText = decimalText(limitControl.value, /^[1-9][0-9]?$/);
    const offsetText = decimalText(offsetControl.value, /^(0|[1-9][0-9]{0,9})$/);
    if (limitText === null || offsetText === null) return null;
    const limit = Number(limitText);
    const offset = Number(offsetText);
    if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) return null;
    if (!Number.isInteger(offset) || offset < 0 || offset > MAX_OFFSET) return null;
    return { status, limit: String(limit), offset: String(offset), sort };
  }

  function validNextOffset(body, selectors) {
    const selectedLimit = Number(selectors.limit);
    const selectedOffset = Number(selectors.offset);
    if (body.has_more === false) return body.next_offset === null;
    if (body.has_more !== true) return false;
    if (typeof body.next_offset !== "number" || !Number.isInteger(body.next_offset)) return false;
    if (body.returned_count !== selectedLimit) return false;
    if (selectedOffset > MAX_OFFSET - selectedLimit) return false;
    return body.next_offset === selectedOffset + selectedLimit && body.next_offset <= MAX_OFFSET;
  }

  function validMetadata(body, selectors) {
    if (!body || typeof body !== "object" || Array.isArray(body)) return false;
    if (!sameKeys(body, BODY_KEYS)) return false;
    const selectedLimit = Number(selectors.limit);
    const selectedOffset = Number(selectors.offset);
    if (body.route !== ROUTE_PATTERN) return false;
    if (body.selected_status !== selectors.status) return false;
    if (body.selected_limit !== selectedLimit) return false;
    if (body.selected_offset !== selectedOffset) return false;
    if (body.selected_sort !== selectors.sort || !SORT_VALUES.has(body.selected_sort)) return false;
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
    if (body.limit !== selectedLimit) return false;
    if (typeof body.returned_count !== "number" || !Number.isInteger(body.returned_count) || body.returned_count < 0) return false;
    if (body.returned_count > selectedLimit) return false;
    if (!validNextOffset(body, selectors)) return false;
    if (!Array.isArray(body.items) || body.items.length > selectedLimit) return false;
    if (body.items.length !== body.returned_count) return false;
    return body.items.every((row) => validRow(row, selectors.status));
  }

  function selectedRoute(selectors) {
    return `${ROUTE}?status=${selectors.status}&limit=${selectors.limit}&offset=${selectors.offset}&sort=${selectors.sort}`;
  }

  function authorityFor(state) {
    return state === "healthy" ? "authoritative" : "non-authoritative";
  }

  function previousOffsetFromSelectors(selectors) {
    if (!selectors) return null;
    const limit = Number(selectors.limit);
    const offset = Number(selectors.offset);
    return offset > 0 ? Math.max(offset - limit, 0) : null;
  }

  function updateNavigation(status, limit, offset, sort, previousOffset, nextOffset) {
    navigationState.status = status;
    navigationState.limit = limit;
    navigationState.offset = offset;
    navigationState.sort = sort;
    navigationState.previousOffset = previousOffset;
    navigationState.nextOffset = nextOffset;
    const previousButton = element(PREVIOUS_CONTROL_ID);
    const nextButton = element(NEXT_CONTROL_ID);
    if (previousButton) previousButton.disabled = loadInFlight || previousOffset === null;
    if (nextButton) nextButton.disabled = loadInFlight || nextOffset === null;
  }

  function disableNavigation() {
    updateNavigation(null, null, null, null, null, null);
  }

  function setControlsLoading(isLoading) {
    loadInFlight = isLoading;
    const loadButton = element(LOAD_CONTROL_ID);
    const previousButton = element(PREVIOUS_CONTROL_ID);
    const nextButton = element(NEXT_CONTROL_ID);
    if (loadButton) loadButton.disabled = isLoading;
    if (isLoading) {
      if (previousButton) previousButton.disabled = true;
      if (nextButton) nextButton.disabled = true;
    }
  }

  function selectorsMatchNavigation(selectors) {
    return navigationState.status === selectors.status && navigationState.limit === Number(selectors.limit) && navigationState.offset === Number(selectors.offset) && navigationState.sort === selectors.sort;
  }

  function refreshNavigationForSelectorEdit() {
    const selectors = readSelectors();
    const hasLoadedNavigation = navigationState.status !== null || navigationState.limit !== null || navigationState.offset !== null || navigationState.sort !== null;
    if (!selectors) {
      if (hasLoadedNavigation || selectorEditInvalidated) {
        selectorEditInvalidated = true;
        renderClosed("invalid", "invalid visible aggregate task-list status, limit, offset, or sort selector; not authoritative.", null);
      } else {
        disableNavigation();
      }
      return;
    }
    if (selectorEditInvalidated && !hasLoadedNavigation) {
      renderClosed("invalid", "visible aggregate task-list status, limit, offset, or sort selector changed after the last authoritative read; reload required before manual pagination.", selectors);
      return;
    }
    if (hasLoadedNavigation && !selectorsMatchNavigation(selectors)) {
      selectorEditInvalidated = true;
      renderClosed("invalid", "visible aggregate task-list status, limit, offset, or sort selector changed after the last authoritative read; reload required before manual pagination.", selectors);
      return;
    }
    const previousOffset = previousOffsetFromSelectors(selectors);
    navigationState.previousOffset = previousOffset;
    const previousButton = element(PREVIOUS_CONTROL_ID);
    const nextButton = element(NEXT_CONTROL_ID);
    if (previousButton) previousButton.disabled = loadInFlight || previousOffset === null;
    if (nextButton) nextButton.disabled = loadInFlight || !(selectors && navigationState.nextOffset !== null && selectorsMatchNavigation(selectors));
  }

  function navigationFromBody(body, selectors) {
    if (body.display_state !== "healthy" || body.authority_state !== "authoritative") {
      disableNavigation();
      selectorEditInvalidated = true;
      return;
    }
    const limit = Number(selectors.limit);
    const offset = Number(selectors.offset);
    const previousOffset = previousOffsetFromSelectors(selectors);
    const nextOffset = body.has_more === true && typeof body.next_offset === "number" ? body.next_offset : null;
    selectorEditInvalidated = false;
    updateNavigation(selectors.status, limit, offset, selectors.sort, previousOffset, nextOffset);
  }

  function render(state, authority, freshness, provenance, correlation, selectedStatus, selectedLimit, selectedOffset, selectedSort, runtimeRoute, pagination, degraded, count, rows) {
    write("aggregate-task-list-status", `Aggregate task list state: ${state}.`);
    write("aggregate-task-list-source", `Source: ${ROUTE_PATTERN}. Runtime route: ${runtimeRoute}.`);
    write("aggregate-task-list-selected-status", `Selected status: ${selectedStatus}.`);
    write("aggregate-task-list-selected-limit", `Selected limit: ${selectedLimit}.`);
    write("aggregate-task-list-selected-offset", `Selected offset: ${selectedOffset}.`);
    write("aggregate-task-list-selected-sort", `Selected sort: ${selectedSort}.`);
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
    disableNavigation();
    selectorEditInvalidated = true;
    const selectedStatus = selectors ? selectors.status : "unavailable";
    const selectedLimit = selectors ? selectors.limit : "unavailable";
    const selectedOffset = selectors ? selectors.offset : "unavailable";
    const selectedSort = selectors ? selectors.sort : "unavailable";
    const runtimeRoute = selectors ? selectedRoute(selectors) : ROUTE_PATTERN;
    render(state, "non-authoritative", "missing server freshness", "backend task summary list", "not provided", selectedStatus, selectedLimit, selectedOffset, selectedSort, runtimeRoute, "selected sorted window unavailable; manual previous/next controls disabled", state, "0", detail);
  }

  function rowText(row) {
    const event = row.last_event;
    const eventText = event ? `last_event ${event.id} ${event.type} ${event.emitted_at} trace ${label(event.trace_id, "not provided")}` : "last_event none";
    return `${row.task_id} ${row.status} ${label(row.title, "untitled")} created ${row.created_at} updated ${row.updated_at} state_since ${row.state_since} actor ${row.actor.kind}/${row.actor.id} ${eventText}`;
  }

  function renderBody(body, selectors) {
    if (!validMetadata(body, selectors)) {
      renderClosed("invalid", "invalid aggregate task list status, limit, offset, and sort response; not authoritative.", selectors);
      return false;
    }
    const state = body.display_state;
    navigationFromBody(body, selectors);
    const authority = authorityFor(state);
    const correlation = label(body.correlation_id, label(body.request_id, label(body.trace_id, "not provided")));
    const provenance = label(body.provenance, "backend task summary list");
    const nextOffset = body.next_offset === null ? "none" : String(body.next_offset);
    const manualNext = navigationState.nextOffset === null ? "disabled" : `enabled to ${navigationState.nextOffset}`;
    const manualPrevious = navigationState.previousOffset === null ? "disabled" : `enabled to ${navigationState.previousOffset}`;
    const pagination = `selected status ${body.selected_status}; selected limit ${body.selected_limit}; selected offset ${body.selected_offset}; selected sort ${body.selected_sort}; returned ${body.returned_count}; has_more ${body.has_more}; next_offset ${nextOffset}; manual_previous ${manualPrevious}; manual_next ${manualNext}`;
    const runtimeRoute = selectedRoute(selectors);
    if (state === "empty-list") {
      render(state, "non-authoritative", body.retrieved_at, provenance, correlation, String(body.selected_status), String(body.selected_limit), String(body.selected_offset), String(body.selected_sort), runtimeRoute, pagination, state, "0", "empty successful read; no task rows returned.");
      return false;
    }
    if (state !== "healthy") {
      render(state, "non-authoritative", body.retrieved_at, provenance, correlation, String(body.selected_status), String(body.selected_limit), String(body.selected_offset), String(body.selected_sort), runtimeRoute, pagination, state, String(body.returned_count), `${state} aggregate task list status, limit, offset, and sort response; not authoritative.`);
      return false;
    }
    render(state, authority, body.retrieved_at, provenance, correlation, String(body.selected_status), String(body.selected_limit), String(body.selected_offset), String(body.selected_sort), runtimeRoute, pagination, "none", String(body.returned_count), body.items.map(rowText).join("\n"));
    return true;
  }

  async function loadAggregateTaskList() {
    if (loadInFlight) return undefined;
    const selectors = readSelectors();
    if (!selectors) {
      renderClosed("invalid", "invalid visible aggregate task-list status, limit, offset, or sort selector; not authoritative.", null);
      return undefined;
    }
    const route = selectedRoute(selectors);
    let canRefreshNavigation = false;
    setControlsLoading(true);
    selectorEditInvalidated = false;
    try {
      const response = await fetch(route, { method: "GET", credentials: "omit" });
      if (!response.ok) {
        const state = response.status === 401 || response.status === 403 ? "unauthorized" : "backend-unavailable";
        renderClosed(state, `${state.replace(/-/g, " ")} response for aggregate task list status, limit, offset, and sort read; not authoritative.`, selectors);
        return undefined;
      }
      let body;
      try {
        body = await response.json();
      } catch (_error) {
        renderClosed("invalid", "invalid aggregate task list status, limit, offset, and sort response; not authoritative.", selectors);
        return undefined;
      }
      canRefreshNavigation = renderBody(body, selectors);
      return undefined;
    } catch (_error) {
      renderClosed("backend-unavailable", "backend unavailable for aggregate task list status, limit, offset, and sort read; not authoritative.", selectors);
      return undefined;
    } finally {
      setControlsLoading(false);
      if (canRefreshNavigation) refreshNavigationForSelectorEdit();
    }
  }

  function setOffsetAndLoad(offset) {
    if (loadInFlight) return undefined;
    const offsetControl = element(OFFSET_CONTROL_ID);
    if (!offsetControl) return undefined;
    offsetControl.value = String(offset);
    return loadAggregateTaskList();
  }

  function loadPreviousOffset() {
    if (loadInFlight) return undefined;
    const selectors = readSelectors();
    if (!selectors) {
      renderClosed("invalid", "invalid visible aggregate task-list status, limit, offset, or sort selector; not authoritative.", null);
      return undefined;
    }
    if (navigationState.previousOffset === null || !selectorsMatchNavigation(selectors)) {
      refreshNavigationForSelectorEdit();
      return undefined;
    }
    return setOffsetAndLoad(navigationState.previousOffset);
  }

  function loadNextOffset() {
    if (loadInFlight) return undefined;
    const selectors = readSelectors();
    if (!selectors) {
      renderClosed("invalid", "invalid visible aggregate task-list status, limit, offset, or sort selector; not authoritative.", null);
      return undefined;
    }
    if (navigationState.nextOffset === null || !selectorsMatchNavigation(selectors)) {
      refreshNavigationForSelectorEdit();
      return undefined;
    }
    return setOffsetAndLoad(navigationState.nextOffset);
  }

  function startAggregateTaskList() {
    disableNavigation();
    const loadButton = element(LOAD_CONTROL_ID);
    const statusControl = element(STATUS_CONTROL_ID);
    const limitControl = element(LIMIT_CONTROL_ID);
    const offsetControl = element(OFFSET_CONTROL_ID);
    const sortControl = element(SORT_CONTROL_ID);
    const previousButton = element(PREVIOUS_CONTROL_ID);
    const nextButton = element(NEXT_CONTROL_ID);
    if (loadButton && typeof loadButton.addEventListener === "function") {
      loadButton.addEventListener("click", loadAggregateTaskList);
    }
    for (const control of [statusControl, limitControl, offsetControl, sortControl]) {
      if (control && typeof control.addEventListener === "function") {
        control.addEventListener("input", refreshNavigationForSelectorEdit);
        control.addEventListener("change", refreshNavigationForSelectorEdit);
      }
    }
    if (previousButton && typeof previousButton.addEventListener === "function") {
      previousButton.addEventListener("click", loadPreviousOffset);
    }
    if (nextButton && typeof nextButton.addEventListener === "function") {
      nextButton.addEventListener("click", loadNextOffset);
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
