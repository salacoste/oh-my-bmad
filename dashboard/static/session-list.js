(function () {
  "use strict";

  const ROUTE = "/v1/sessions";
  const ROUTE_PATTERN = "GET /v1/sessions";
  const MAX_LIMIT = 50;
  const SORT = "last_heartbeat_at_desc_nulls_last_started_at_desc_id_asc";
  const TOP_KEYS = ["route", "retrieved_at", "freshness_state", "display_state", "authority_state", "provenance", "request_id", "trace_id", "correlation_id", "limit", "returned_count", "has_more", "next_offset", "sort", "items"];
  const ROW_KEYS = ["session_id", "task_id", "worker_kind", "status", "started_at", "ended_at", "last_heartbeat_at", "heartbeat_state"];
  const ALLOWED_DISPLAY_STATES = new Set(["healthy", "empty-list"]);
  const ALLOWED_HEARTBEAT_STATES = new Set(["ended", "observed", "missing"]);
  const UTC_TIMESTAMP_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|\+00:00)$/;

  function element(id) {
    return document.getElementById(id);
  }

  function write(id, value) {
    const target = element(id);
    if (target) target.textContent = value;
  }

  function label(value, placeholder) {
    if (typeof value !== "string") return placeholder;
    const text = value.trim();
    return text || placeholder;
  }

  function sameKeys(value, allowed) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    const keys = Object.keys(value).sort();
    const expected = allowed.slice().sort();
    return keys.length === expected.length && keys.every((key, index) => key === expected[index]);
  }

  function looksPathLike(value) {
    return typeof value === "string" && (value.includes("/") || value.includes("\\"));
  }

  function leapYear(year) {
    return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  }

  function daysInMonth(year, month) {
    if (month === 2) return leapYear(year) ? 29 : 28;
    if ([4, 6, 9, 11].includes(month)) return 30;
    return 31;
  }

  function validTimestamp(value, nullable) {
    if (value === null) return Boolean(nullable);
    if (typeof value !== "string" || looksPathLike(value)) return false;
    const match = UTC_TIMESTAMP_RE.exec(value);
    if (!match) return false;
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const hour = Number(match[4]);
    const minute = Number(match[5]);
    const second = Number(match[6]);
    return Boolean(
      month >= 1 &&
        month <= 12 &&
        day >= 1 &&
        day <= daysInMonth(year, month) &&
        hour >= 0 &&
        hour <= 23 &&
        minute >= 0 &&
        minute <= 59 &&
        second >= 0 &&
        second <= 59
    );
  }

  function validDisplayString(value) {
    return typeof value === "string" && value.length > 0 && !looksPathLike(value);
  }

  function validNullableDisplayString(value) {
    return value === null || validDisplayString(value);
  }

  function jsonContentType(response) {
    if (!response.headers || typeof response.headers.get !== "function") return false;
    const contentType = response.headers.get("content-type");
    return typeof contentType === "string" && contentType.toLowerCase().includes("application/json");
  }

  function timeoutSignal() {
    if (typeof AbortSignal === "undefined" || typeof AbortSignal.timeout !== "function") {
      return undefined;
    }
    return AbortSignal.timeout(8000);
  }

  function validRow(row) {
    return Boolean(
      sameKeys(row, ROW_KEYS) &&
        validDisplayString(row.session_id) &&
        validDisplayString(row.task_id) &&
        validDisplayString(row.worker_kind) &&
        validDisplayString(row.status) &&
        validTimestamp(row.started_at, false) &&
        validTimestamp(row.ended_at, true) &&
        validTimestamp(row.last_heartbeat_at, true) &&
        ALLOWED_HEARTBEAT_STATES.has(row.heartbeat_state)
    );
  }

  function validMetadata(body) {
    if (!body || typeof body !== "object" || Array.isArray(body)) return false;
    if (!sameKeys(body, TOP_KEYS)) return false;
    if (body.route !== ROUTE_PATTERN) return false;
    if (body.freshness_state !== "fresh") return false;
    if (!ALLOWED_DISPLAY_STATES.has(body.display_state)) return false;
    if (body.authority_state !== "authoritative" && body.authority_state !== "non-authoritative") return false;
    if (body.display_state === "healthy" && body.authority_state !== "authoritative") return false;
    if (body.display_state === "empty-list" && body.authority_state !== "non-authoritative") return false;
    if (!validTimestamp(body.retrieved_at, false)) return false;
    if (body.provenance !== "registry-state session summary list") return false;
    if (!validDisplayString(body.request_id)) return false;
    if (!validNullableDisplayString(body.trace_id)) return false;
    if (!validDisplayString(body.correlation_id)) return false;
    if (body.correlation_id !== body.request_id) return false;
    if (typeof body.limit !== "number" || body.limit !== MAX_LIMIT) return false;
    if (typeof body.returned_count !== "number" || body.returned_count < 0) return false;
    if (typeof body.has_more !== "boolean") return false;
    if (body.next_offset !== null) return false;
    if (body.sort !== SORT) return false;
    if (!Array.isArray(body.items) || body.items.length > body.limit) return false;
    if (body.items.length !== body.returned_count) return false;
    if (body.display_state === "healthy" && body.items.length === 0) return false;
    if (body.display_state === "empty-list" && body.items.length !== 0) return false;
    return body.items.every(validRow);
  }

  function render(state, authority, freshness, provenance, correlation, pagination, degraded, count, rows) {
    write("session-list-status", `Session list state: ${state}.`);
    write("session-list-source", `Source: ${ROUTE_PATTERN}. Runtime route: ${ROUTE_PATTERN}.`);
    write("session-list-freshness", `Freshness: ${freshness}.`);
    write("session-list-authority", `Authority: ${authority}.`);
    write("session-list-provenance", `Provenance: ${provenance}.`);
    write("session-list-correlation", `Correlation: ${correlation}.`);
    write("session-list-pagination", `Pagination: ${pagination}.`);
    write("session-list-degraded", `Degraded state: ${degraded}.`);
    write("session-list-count", `Session rows: ${count}.`);
    write("session-list-rows", rows);
  }

  function renderClosed(state, detail) {
    render(state, "non-authoritative", "missing server freshness", "backend session summary list", "not provided", "fixed first page unavailable", state, "0", detail);
  }

  function rowText(row) {
    return `${row.session_id} task ${row.task_id} worker ${row.worker_kind} status ${row.status} started ${row.started_at} ended ${label(row.ended_at, "not ended")} heartbeat ${label(row.last_heartbeat_at, "not observed")} heartbeat_state ${row.heartbeat_state}`;
  }

  function renderBody(body) {
    if (!validMetadata(body)) {
      renderClosed("invalid", "invalid session list response; not authoritative.");
      return;
    }
    const state = body.display_state;
    const correlation = label(body.correlation_id, label(body.request_id, label(body.trace_id, "not provided")));
    const pagination = `limit ${body.limit}; returned ${body.returned_count}; has_more ${body.has_more}; next_offset none`;
    if (state === "empty-list") {
      render(state, "non-authoritative", body.retrieved_at, body.provenance, correlation, pagination, state, "0", "empty successful read; no session rows returned.");
      return;
    }
    render(state, "authoritative", body.retrieved_at, body.provenance, correlation, pagination, "none", String(body.returned_count), body.items.map(rowText).join("\n"));
  }

  async function loadSessionList() {
    try {
      const signal = timeoutSignal();
      const response = await fetch(ROUTE, { method: "GET", headers: { Accept: "application/json" }, signal });
      if (!response.ok) {
        const state = response.status === 401 || response.status === 403 ? "unauthorized" : "backend-unavailable";
        renderClosed(state, `${state.replace(/-/g, " ")} response for session list; not authoritative.`);
        return;
      }
      if (!jsonContentType(response)) {
        renderClosed("invalid", "invalid session list response; not authoritative.");
        return;
      }
      let body;
      try {
        body = await response.json();
      } catch (_error) {
        renderClosed("invalid", "invalid session list response; not authoritative.");
        return;
      }
      renderBody(body);
    } catch (_error) {
      renderClosed("backend-unavailable", "backend unavailable for session list; not authoritative.");
    }
  }

  function startSessionList() {
    return loadSessionList();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startSessionList);
  } else {
    const pending = startSessionList();
    if (typeof window !== "undefined") window.__sessionListReady = pending;
  }
})();
