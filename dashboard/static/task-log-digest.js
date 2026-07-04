(function () {
  "use strict";

  const ROUTE_PREFIX = "/v1/tasks/";
  const ROUTE_SUFFIX = "/logs/digest";
  const ROUTE_PATTERN = "GET /v1/tasks/{task_id}/logs/digest";
  const ALLOWED_DISPLAY_STATES = new Set([
    "healthy",
    "unavailable",
    "stale",
    "invalid",
    "unauthorized",
    "backend-unavailable",
    "provider-unavailable",
    "empty-digest",
  ]);
  const ALLOWED_FRESHNESS_STATES = new Set(["fresh", "stale"]);

  function element(id) {
    return document.getElementById(id);
  }

  function write(id, value) {
    const target = element(id);
    if (target) target.textContent = value;
  }

  function visibleTaskId() {
    const source = element("task-log-digest-task-id-source");
    return source ? source.textContent.trim() : "";
  }

  function label(value, fallback) {
    if (typeof value !== "string") return fallback;
    const text = value.trim();
    return text || fallback;
  }

  function normalizeState(value) {
    return label(value, "").replace(/-/g, "-");
  }

  function displayState(value) {
    const state = normalizeState(value);
    return ALLOWED_DISPLAY_STATES.has(state) ? state : "invalid";
  }

  function hasAllowedFreshnessState(body) {
    const state = label(body.freshness_state, "");
    return !state || ALLOWED_FRESHNESS_STATES.has(state);
  }

  function render(state, authority, detail, taskId, route, freshness, provenance, correlation, degraded) {
    const taskText = taskId || "missing";
    const routeText = route || ROUTE_PATTERN;
    write("task-log-digest-status", `Task log digest state: ${state}.`);
    write("task-log-digest-source", `Source: ${ROUTE_PATTERN}. Runtime route: ${routeText}.`);
    write("task-log-digest-task-id", `task_id: ${taskText}.`);
    write("task-log-digest-freshness", `Freshness: ${freshness}.`);
    write("task-log-digest-authority", `Authority: ${authority}.`);
    write("task-log-digest-provenance", `Provenance: ${provenance}.`);
    write("task-log-digest-correlation", `Correlation: ${correlation}.`);
    write("task-log-digest-degraded", `Degraded state: ${degraded}.`);
    write("task-log-digest-detail", `Digest: ${detail}`);
  }

  function noFetch(state, detail, taskId) {
    render(state, "non-authoritative", detail, taskId, ROUTE_PATTERN, "pending", "pending", "pending", state);
  }

  function readFailureState(status) {
    return status === 401 || status === 403 ? "unauthorized" : "backend-unavailable";
  }

  function digestText(body) {
    return label(body.digest, label(body.summary, ""));
  }

  function hasServerFreshness(body) {
    return Boolean(
      body &&
        typeof body === "object" &&
        (label(body.retrieved_at, "") || label(body.completed_at, "")) &&
        ALLOWED_FRESHNESS_STATES.has(label(body.freshness_state, ""))
    );
  }

  function hasValidHealthyShape(body, taskId) {
    return Boolean(
      body &&
        typeof body === "object" &&
        body.task_id === taskId &&
        digestText(body) &&
        hasServerFreshness(body)
    );
  }

  function stateFromBody(body, taskId) {
    if (!body || typeof body !== "object") return "invalid";
    if (!hasAllowedFreshnessState(body)) return "invalid";
    if (body.display_state) {
      const state = displayState(body.display_state);
      if (state === "healthy" && !hasValidHealthyShape(body, taskId)) return "invalid";
      return state;
    }
    if (body.freshness_state === "stale") return "stale";
    if (body.task_id === taskId && !digestText(body)) return "empty-digest";
    if (!hasValidHealthyShape(body, taskId)) return "invalid";
    return "healthy";
  }

  function authorityFor(state) {
    return state === "healthy" ? "authoritative" : "non-authoritative";
  }

  function freshnessFor(body) {
    return label(body.retrieved_at, label(body.completed_at, "missing server freshness"));
  }

  function provenanceFor(body) {
    return label(body.provenance, label(body.authority_state, "backend digest response"));
  }

  function correlationFor(body) {
    return label(body.correlation_id, label(body.request_id, label(body.trace_id, "not provided")));
  }

  function degradedFor(body, state) {
    return label(body.degraded_reason, state);
  }

  function detailFor(body, state) {
    if (!body || typeof body !== "object") return "invalid digest response shape; not authoritative.";
    if (state !== "healthy") return `${state} digest response; ${degradedFor(body, state)}; not authoritative.`;
    return `authoritative digest text: ${digestText(body)}`;
  }

  async function loadTaskLogDigest() {
    const taskId = visibleTaskId();
    if (!taskId) {
      noFetch("missing task_id", "missing task_id visible source; no fetch attempted.", "missing");
      return;
    }

    const route = ROUTE_PREFIX + encodeURIComponent(taskId) + ROUTE_SUFFIX;
    try {
      const response = await fetch(route, { method: "GET" });
      if (!response.ok) {
        const state = readFailureState(response.status);
        render(state, "non-authoritative", `${state.replace(/-/g, " ")} response for task log digest; not authoritative.`, taskId, `GET ${route}`, "missing server freshness", "backend digest response", "not provided", state);
        return;
      }
      let body;
      try {
        body = await response.json();
      } catch (_error) {
        render("invalid", "non-authoritative", "invalid task log digest response; not authoritative.", taskId, `GET ${route}`, "missing server freshness", "backend digest response", "not provided", "invalid");
        return;
      }
      const state = stateFromBody(body, taskId);
      const authority = authorityFor(state);
      render(state, authority, detailFor(body, state), taskId, `GET ${route}`, freshnessFor(body), provenanceFor(body), correlationFor(body), degradedFor(body, state));
    } catch (_error) {
      render("backend-unavailable", "non-authoritative", "backend unavailable for task log digest; not authoritative.", taskId, `GET ${route}`, "missing server freshness", "backend digest response", "not provided", "backend-unavailable");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadTaskLogDigest);
  } else {
    loadTaskLogDigest();
  }
})();
