(function () {
  "use strict";

  const ROUTE_PREFIX = "/v1/tasks/";
  const EVENTS_SUFFIX = "/events";
  const TRANSITIONS_SUFFIX = "/transitions";
  const EVENTS_PATTERN = "GET /v1/tasks/{task_id}/events";
  const TRANSITIONS_PATTERN = "GET /v1/tasks/{task_id}/transitions";

  function element(id) {
    return document.getElementById(id);
  }

  function write(id, value) {
    const target = element(id);
    if (target) target.textContent = value;
  }

  function visibleText(id) {
    const source = element(id);
    return source ? source.textContent.trim() : "";
  }

  function visibleTaskId() {
    return visibleText("event-timeline-task-id-source");
  }

  function render(state, authority, detail, taskId, routesText, freshness, eventCount, transitionCount) {
    const taskText = taskId || "missing";
    write("event-timeline-status", `Event timeline state: ${state}.`);
    write("event-timeline-source", `Source: ${EVENTS_PATTERN} and ${TRANSITIONS_PATTERN}. Runtime routes: ${routesText}.`);
    write("event-timeline-task-id", `task_id: ${taskText}.`);
    write("event-timeline-freshness", `Freshness: ${freshness}.`);
    write("event-timeline-authority", `Authority: ${authority}.`);
    write("event-timeline-event-count", `Event rows: ${eventCount}.`);
    write("event-timeline-transition-count", `Transition rows: ${transitionCount}.`);
    write("event-timeline-detail", `Detail: ${detail}`);
  }

  function validRows(rows, taskId) {
    return Array.isArray(rows) && rows.every((row) => row && typeof row === "object" && row.task_id === taskId && typeof row.event_id === "string" && row.event_id.trim());
  }

  function stateFromBody(body, taskId, rowsKey) {
    if (!body || typeof body !== "object" || body.task_id !== taskId) return "invalid";
    const displayState = body.display_state ? String(body.display_state).replace(/-/g, " ") : "";
    if (displayState && displayState !== "healthy") return displayState;
    if (body.freshness_state === "stale") return "stale";
    if (!validRows(body[rowsKey], taskId)) return "invalid";
    return body[rowsKey].length ? "healthy" : "empty";
  }

  function readFailureState(status) {
    return status === 401 || status === 403 ? "unauthorized" : "backend unavailable";
  }

  async function readRoute(route, rowsKey, taskId) {
    try {
      const response = await fetch(route, { method: "GET" });
      if (!response.ok) {
        return {
          state: readFailureState(response.status),
          rows: 0,
          freshness: new Date().toISOString(),
        };
      }
      let body;
      try {
        body = await response.json();
      } catch (_error) {
        return { state: "invalid", rows: 0, freshness: new Date().toISOString() };
      }
      return {
        state: stateFromBody(body, taskId, rowsKey),
        rows: body && typeof body === "object" && Array.isArray(body[rowsKey]) ? body[rowsKey].length : 0,
        freshness: body && typeof body === "object" && body.retrieved_at ? body.retrieved_at : new Date().toISOString(),
      };
    } catch (_error) {
      return { state: "backend unavailable", rows: 0, freshness: new Date().toISOString() };
    }
  }

  function combinedState(eventsResult, transitionsResult) {
    const states = [eventsResult.state, transitionsResult.state];
    for (const state of ["unauthorized", "backend unavailable", "invalid", "stale", "partial"]) {
      if (states.includes(state)) return state;
    }
    if (states.every((state) => state === "empty")) return "empty";
    return "healthy";
  }

  function authorityFor(state) {
    return state === "healthy" ? "authoritative" : "non-authoritative";
  }

  async function loadEventTimeline() {
    const taskId = visibleTaskId();
    if (!taskId) {
      render("missing task_id", "non-authoritative", "missing task_id visible source; no fetch attempted.", "missing", `${EVENTS_PATTERN}; ${TRANSITIONS_PATTERN}`, "pending", "pending", "pending");
      return;
    }

    const encodedTaskId = encodeURIComponent(taskId);
    const eventsRoute = ROUTE_PREFIX + encodedTaskId + EVENTS_SUFFIX;
    const transitionsRoute = ROUTE_PREFIX + encodedTaskId + TRANSITIONS_SUFFIX;
    const [eventsResult, transitionsResult] = await Promise.all([
      readRoute(eventsRoute, "events", taskId),
      readRoute(transitionsRoute, "transitions", taskId),
    ]);
    const state = combinedState(eventsResult, transitionsResult);
    const eventCount = eventsResult.rows;
    const transitionCount = transitionsResult.rows;
    const totalRows = eventCount + transitionCount;
    const freshness = eventsResult.freshness || transitionsResult.freshness || new Date().toISOString();
    const detail = state === "healthy"
      ? `authoritative success for ${totalRows} rows; event_id metadata only.`
      : `${state} event timeline response; ${totalRows} rows; event_id metadata only; not authoritative.`;
    render(state, authorityFor(state), detail, taskId, `GET ${eventsRoute}; GET ${transitionsRoute}`, freshness, eventCount, transitionCount);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadEventTimeline);
  } else {
    loadEventTimeline();
  }
})();
