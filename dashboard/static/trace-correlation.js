(function () {
  "use strict";

  const ROUTE_PREFIX = "/v1/trace/";
  const TRACE_PATTERN = "GET /v1/trace/{trace_id}";

  function element(id) {
    return document.getElementById(id);
  }

  function write(id, value) {
    const target = element(id);
    if (target) target.textContent = value;
  }

  function visible_trace_id() {
    const source = element("trace-correlation-trace-id-source");
    return source ? source.textContent.trim() : "";
  }

  function render(state, authority, detail, trace_id, route_text, freshness, row_count, linked_text) {
    const trace_text = trace_id || "missing";
    write("trace-correlation-status", `Trace correlation state: ${state}.`);
    write("trace-correlation-source", `Source: ${TRACE_PATTERN}. Runtime route: ${route_text}.`);
    write("trace-correlation-trace-id", `trace_id: ${trace_text}.`);
    write("trace-correlation-freshness", `Freshness: ${freshness}.`);
    write("trace-correlation-authority", `Authority: ${authority}.`);
    write("trace-correlation-row-count", `Trace rows: ${row_count}.`);
    write("trace-correlation-linked-identifiers", `Linked identifiers: ${linked_text}.`);
    write("trace-correlation-detail", `Detail: ${detail}`);
  }

  function rows_from_body(body) {
    if (!body || typeof body !== "object" || !Array.isArray(body.events)) return [];
    return body.events;
  }

  function valid_rows(rows, trace_id) {
    return Array.isArray(rows) && rows.every((row) => row && typeof row === "object" && row.trace_id === trace_id);
  }

  function state_from_body(body, trace_id) {
    if (!body || typeof body !== "object" || body.trace_id !== trace_id) return "invalid";
    const rows = rows_from_body(body);
    if (!valid_rows(rows, trace_id)) return "invalid";
    const display_state = body.display_state ? String(body.display_state).replace(/-/g, " ") : "";
    if (display_state && display_state !== "healthy") return display_state;
    if (body.freshness_state === "stale") return "stale";
    return rows.length ? "healthy" : "empty";
  }

  function linked_identifiers(rows) {
    const values = [];
    for (const row of rows) {
      if (row.event_id) values.push(`event_id=${row.event_id}`);
      if (row.task_id) values.push(`task_id=${row.task_id}`);
      if (row.session_id) values.push(`session_id=${row.session_id}`);
    }
    return values.length ? values.join(", ") : "none returned";
  }

  async function readRoute(route, trace_id) {
    try {
      const response = await fetch(route, { method: "GET" });
      if (!response.ok) {
        return {
          state: response.status === 401 || response.status === 403 ? "unauthorized" : "backend unavailable",
          rows: [],
          freshness: "not returned",
        };
      }
      let body;
      try {
        body = await response.json();
      } catch (_error) {
        return { state: "invalid", rows: [], freshness: "not returned" };
      }
      const rows = rows_from_body(body);
      const state = state_from_body(body, trace_id);
      return {
        state,
        rows: state === "invalid" ? [] : rows,
        freshness: body && typeof body === "object" && body.retrieved_at ? body.retrieved_at : "not returned",
      };
    } catch (_error) {
      return { state: "backend unavailable", rows: [], freshness: "not returned" };
    }
  }

  function authority_for(state) {
    return state === "healthy" ? "authoritative" : "non-authoritative";
  }

  async function loadTraceCorrelation() {
    const trace_id = visible_trace_id();
    if (!trace_id) {
      render("missing trace_id", "non-authoritative", "missing trace_id visible source; no fetch attempted.", "missing", TRACE_PATTERN, "pending", "pending", "pending");
      return;
    }

    const route = ROUTE_PREFIX + encodeURIComponent(trace_id);
    const result = await readRoute(route, trace_id);
    const state = result.state;
    const rows = result.rows;
    const row_count = rows.length;
    const linked_text = linked_identifiers(rows);
    const detail = state === "healthy"
      ? `authoritative success for ${row_count} rows; event_id, task_id, and session_id metadata only.`
      : `${state} trace correlation response; ${row_count} rows; event_id, task_id, and session_id metadata only; not authoritative.`;
    render(state, authority_for(state), detail, trace_id, `GET ${route}`, result.freshness, row_count, linked_text);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadTraceCorrelation);
  } else {
    loadTraceCorrelation();
  }
})();
