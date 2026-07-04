(function () {
  "use strict";

  const ROUTE_PREFIX = "/v1/tasks/";
  const ROUTE_PATTERN = "GET /v1/tasks/{task_id}";

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
    return visibleText("task-detail-task-id-source");
  }

  function render(state, authority, detail, taskId, route, freshness) {
    const routeText = route || ROUTE_PATTERN;
    const taskText = taskId || "missing";
    write("task-detail-status", `Task detail state: ${state}.`);
    write("task-detail-source", `Source: ${ROUTE_PATTERN}. Runtime route: ${routeText}.`);
    write("task-detail-task-id", `task_id: ${taskText}.`);
    write("task-detail-freshness", `Freshness: ${freshness}.`);
    write("task-detail-authority", `Authority: ${authority}.`);
    write("task-detail-detail", `Detail: ${detail}`);
  }

  function hasValidHealthyShape(body, taskId) {
    return Boolean(
      body &&
        typeof body === "object" &&
        body.task_id === taskId &&
        typeof body.status === "string" &&
        body.status.trim()
    );
  }

  function stateFromBody(body, taskId) {
    if (!body || typeof body !== "object") return "invalid";
    if (body.display_state) {
      const displayState = String(body.display_state).replace(/-/g, " ");
      if (displayState === "healthy" && !hasValidHealthyShape(body, taskId)) return "invalid";
      return displayState;
    }
    if (body.freshness_state === "stale") return "stale";
    if (!hasValidHealthyShape(body, taskId)) return "invalid";
    return "healthy";
  }

  function authorityFor(state) {
    return state === "healthy" ? "authoritative" : "non-authoritative";
  }

  function readFailureState(status) {
    return status === 401 || status === 403 ? "unauthorized" : "backend unavailable";
  }

  function renderNoFetch(state, detail, taskId) {
    render(state, "non-authoritative", detail, taskId, ROUTE_PATTERN, "pending");
  }

  function renderReadFailure(state, taskId, route, freshness) {
    render(state, "non-authoritative", `${state} response for task detail; not authoritative.`, taskId, route, freshness);
  }

  function detailFor(body, state) {
    if (!body || typeof body !== "object") return "invalid task detail response shape.";
    if (state !== "healthy") return `${state} task detail response; not authoritative.`;
    const title = body.title || "untitled task";
    const status = body.status;
    return `authoritative success for ${title}; status ${status}.`;
  }

  async function loadTaskDetail() {
    const taskId = visibleTaskId();
    if (!taskId) {
      renderNoFetch("missing task_id", "missing task_id visible source; no fetch attempted.", "missing");
      return;
    }

    const route = ROUTE_PREFIX + encodeURIComponent(taskId);
    try {
      const response = await fetch(route, { method: "GET" });
      if (!response.ok) {
        renderReadFailure(readFailureState(response.status), taskId, `GET ${route}`, new Date().toISOString());
        return;
      }
      const body = await response.json();
      const state = stateFromBody(body, taskId);
      const authority = authorityFor(state);
      const freshness = body && typeof body === "object" && body.retrieved_at ? body.retrieved_at : new Date().toISOString();
      render(state, authority, detailFor(body, state), taskId, `GET ${route}`, freshness);
    } catch (_error) {
      render("backend unavailable", "non-authoritative", "backend unavailable or invalid task detail response; not authoritative.", taskId, `GET ${route}`, new Date().toISOString());
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadTaskDetail);
  } else {
    loadTaskDetail();
  }
})();
