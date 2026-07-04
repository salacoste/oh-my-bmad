(function () {
  "use strict";

  const ROUTE_PREFIX = "/v1/sessions/";
  const ROUTE_PATTERN = "GET /v1/sessions/{session_id}";
  const TOP_KEYS = ["route", "selected_session_id", "retrieved_at", "freshness_state", "display_state", "authority_state", "provenance", "request_id", "trace_id", "correlation_id", "item"];
  const ROW_KEYS = ["session_id", "task_id", "worker_kind", "status", "started_at", "ended_at", "last_heartbeat_at", "heartbeat_state"];
  const ALLOWED_HEARTBEAT_STATES = new Set(["ended", "observed", "missing"]);
  const UTC_TIMESTAMP_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|\+00:00)$/;

  function element(id) {
    return document.getElementById(id);
  }

  function write(id, value) {
    const target = element(id);
    if (target) target.textContent = value;
  }

  function readVisibleSessionId() {
    const target = element("session-detail-session-id-source");
    if (!target || typeof target.textContent !== "string") return "";
    return target.textContent.trim();
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


  function validRow(row, selectedSessionId) {
    return Boolean(
      sameKeys(row, ROW_KEYS) &&
        row.session_id === selectedSessionId &&
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

  function validMetadata(body, selectedSessionId) {
    if (!body || typeof body !== "object" || Array.isArray(body)) return false;
    if (!sameKeys(body, TOP_KEYS)) return false;
    if (body.route !== ROUTE_PATTERN) return false;
    if (body.selected_session_id !== selectedSessionId) return false;
    if (body.freshness_state !== "fresh") return false;
    if (body.display_state !== "healthy") return false;
    if (body.authority_state !== "authoritative") return false;
    if (!validTimestamp(body.retrieved_at, false)) return false;
    if (body.provenance !== "registry-state session detail") return false;
    if (!validDisplayString(body.request_id)) return false;
    if (!validNullableDisplayString(body.trace_id)) return false;
    if (!validDisplayString(body.correlation_id)) return false;
    if (body.correlation_id !== body.request_id) return false;
    return validRow(body.item, selectedSessionId);
  }

  function render(state, authority, freshness, provenance, correlation, degraded, row) {
    write("session-detail-status", `Session detail state: ${state}.`);
    write("session-detail-source", `Source: ${ROUTE_PATTERN}. Runtime route: ${ROUTE_PATTERN}.`);
    write("session-detail-freshness", `Freshness: ${freshness}.`);
    write("session-detail-authority", `Authority: ${authority}.`);
    write("session-detail-provenance", `Provenance: ${provenance}.`);
    write("session-detail-correlation", `Correlation: ${correlation}.`);
    write("session-detail-degraded", `Degraded state: ${degraded}.`);
    write("session-detail-row", row);
  }

  function renderClosed(state, detail) {
    render(state, "non-authoritative", "missing server freshness", "backend session detail", "not provided", state, detail);
  }

  function readFailureState(status) {
    if (status === 404) return "not-found";
    return status === 401 || status === 403 ? "unauthorized" : "backend-unavailable";
  }

  function rowText(row) {
    return `${row.session_id} task ${row.task_id} worker ${row.worker_kind} status ${row.status} started ${row.started_at} ended ${label(row.ended_at, "not ended")} heartbeat ${label(row.last_heartbeat_at, "not observed")} heartbeat_state ${row.heartbeat_state}`;
  }

  function renderBody(body, selectedSessionId) {
    if (!validMetadata(body, selectedSessionId)) {
      renderClosed("invalid", "invalid session detail response; not authoritative.");
      return;
    }
    const correlation = label(body.correlation_id, label(body.request_id, label(body.trace_id, "not provided")));
    render("healthy", "authoritative", body.retrieved_at, body.provenance, correlation, "none", rowText(body.item));
  }

  async function loadSessionDetail() {
    const selectedSessionId = readVisibleSessionId();
    if (!selectedSessionId || looksPathLike(selectedSessionId)) {
      renderClosed("unavailable", "missing visible session_id; not authoritative.");
      return;
    }
    const route = ROUTE_PREFIX + encodeURIComponent(selectedSessionId);
    try {
      const response = await fetch(route, { method: "GET", headers: { Accept: "application/json" } });
      if (!response.ok) {
        const state = readFailureState(response.status);
        renderClosed(state, `${state.replace(/-/g, " ")} response for session detail; not authoritative.`);
        return;
      }
      if (!jsonContentType(response)) {
        renderClosed("invalid", "invalid session detail response; not authoritative.");
        return;
      }
      let body;
      try {
        body = await response.json();
      } catch (_error) {
        renderClosed("invalid", "invalid session detail response; not authoritative.");
        return;
      }
      renderBody(body, selectedSessionId);
    } catch (_error) {
      renderClosed("backend-unavailable", "backend unavailable for session detail; not authoritative.");
    }
  }

  function startSessionDetail() {
    return loadSessionDetail();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startSessionDetail);
  } else {
    const pending = startSessionDetail();
    if (typeof window !== "undefined") window.__sessionDetailReady = pending;
  }
})();
