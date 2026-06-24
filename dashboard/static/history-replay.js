(function () {
  "use strict";

  const ROUTE_PREFIX = "/v1/tasks/";
  const ROUTE_SUFFIX = "/history";
  const REPLAY_ROUTE = "/v1/events/replay";
  const VALIDATE_ROUTE = "/v1/events/replay/validate";
  const HISTORY_PATTERN = "GET /v1/tasks/{task_id}/history";
  const REPLAY_PATTERN = "GET /v1/events/replay";
  const VALIDATE_PATTERN = "GET /v1/events/replay/validate";

  function element(id) {
    return document.getElementById(id);
  }

  function write(id, value) {
    const target = element(id);
    if (target) target.textContent = value;
  }

  function visible(id) {
    const source = element(id);
    return source ? source.textContent.trim() : "";
  }

  function render(state, authority, detail, task, target, routes, fresh, historyCount, replayCount, validation, linked) {
    write("history-replay-status", `History/replay state: ${state}.`);
    write("history-replay-source", `Source: ${HISTORY_PATTERN}, ${REPLAY_PATTERN}, and ${VALIDATE_PATTERN}. Runtime routes: ${routes}.`);
    write("history-replay-task-id", `task_id: ${task || "missing"}.`);
    write("history-replay-target", `Replay target: ${target || "missing"}.`);
    write("history-replay-freshness", `Freshness: ${fresh}.`);
    write("history-replay-authority", `Authority: ${authority}.`);
    write("history-replay-history-count", `History events: ${historyCount}.`);
    write("history-replay-replay-count", `Replayed events: ${replayCount}.`);
    write("history-replay-validation-status", `Validation: ${validation}.`);
    write("history-replay-linked-identifiers", `Linked identifiers: ${linked}.`);
    write("history-replay-detail", `Detail: ${detail}`);
  }

  function noFetch(state, detail, task, target) {
    render(state, "non-authoritative", detail, task, target, `${HISTORY_PATTERN}; ${REPLAY_PATTERN}; ${VALIDATE_PATTERN}`, "pending", "pending", "pending", "pending", "pending");
  }

  function replayTarget() {
    const kind = visible("history-replay-target-kind-source");
    const value = visible("history-replay-target-value-source");
    if (!kind || !value) return { ok: false, state: "missing replay target", text: `${kind}=${value}` };
    if (kind !== "to_sequence" && kind !== "to_timestamp") return { ok: false, state: "invalid replay target", text: `${kind}=${value}` };
    return { ok: true, key: kind, value, text: `${kind}=${value}` };
  }

  function rows(body) {
    return body && typeof body === "object" && Array.isArray(body.events) ? body.events : [];
  }

  function validHistory(body, task) {
    if (!body || typeof body !== "object" || body.task_id !== task) return false;
    return rows(body).every((row) => row && typeof row === "object" && row.task_id === task);
  }

  function validationState(status) {
    if (!status) return "invalid";
    const normalized = String(status).replace(/-/g, " ").toLowerCase();
    if (normalized === "match" || normalized === "success" || normalized === "ok" || normalized === "healthy") return "healthy";
    if (normalized === "empty") return "empty";
    if (normalized === "partial" || normalized === "stale" || normalized === "invalid") return normalized;
    if (normalized === "mismatch" || normalized === "replay validation mismatch") return "replay validation mismatch";
    return "invalid";
  }

  function stateFor(body, task, kind) {
    if (kind === "history" && !validHistory(body, task)) return "invalid";
    if (!body || typeof body !== "object") return "invalid";
    const display = body.display_state ? String(body.display_state).replace(/-/g, " ") : "";
    if (display && display !== "healthy") return display;
    if (body.freshness_state === "stale") return "stale";
    if (kind === "validation") return validationState(body.validation_status);
    if (kind === "history") return rows(body).length ? "healthy" : "empty";
    if (kind === "replay" && Number(body.replayed_event_count || 0) === 0) return "empty";
    return "healthy";
  }

  async function read(route, task, kind) {
    try {
      const response = await fetch(route, { method: "GET" });
      if (!response.ok) {
        return { state: response.status === 401 || response.status === 403 ? "unauthorized" : "backend unavailable", body: null, count: 0, fresh: "not returned" };
      }
      let body;
      try {
        body = await response.json();
      } catch (_error) {
        return { state: "invalid", body: null, count: 0, fresh: "not returned" };
      }
      const state = stateFor(body, task, kind);
      const count = kind === "history" ? rows(body).length : Number(body.replayed_event_count || 0);
      return { state, body, count, fresh: body && body.retrieved_at ? body.retrieved_at : "not returned" };
    } catch (_error) {
      return { state: "backend unavailable", body: null, count: 0, fresh: "not returned" };
    }
  }

  function combine(results) {
    const states = results.map((result) => result.state);
    for (const state of ["unauthorized", "backend unavailable", "invalid", "replay validation mismatch", "partial", "stale"]) {
      if (states.includes(state)) return state;
    }
    if (states.every((state) => state === "empty")) return "empty";
    return "healthy";
  }

  function linked(historyBody, replayBody, validateBody) {
    const values = [];
    for (const row of rows(historyBody)) {
      if (row.event_id) values.push(`event_id=${row.event_id}`);
      if (row.trace_id) values.push(`trace_id=${row.trace_id}`);
    }
    for (const body of [replayBody, validateBody]) {
      if (body && typeof body === "object" && body.replay_id) values.push(`replay_id=${body.replay_id}`);
      if (body && typeof body === "object" && body.event_id) values.push(`event_id=${body.event_id}`);
      if (body && typeof body === "object" && body.trace_id) values.push(`trace_id=${body.trace_id}`);
    }
    return values.length ? values.join(", ") : "none returned";
  }

  async function loadHistoryReplay() {
    const task = visible("history-replay-task-id-source");
    const target = replayTarget();
    if (!task) {
      noFetch("missing task_id", "missing task_id visible source; no fetch attempted.", "missing", target.text || "missing");
      return;
    }
    if (!target.ok) {
      noFetch(target.state, `${target.state} visible source; no fetch attempted.`, task, target.text);
      return;
    }

    const route = ROUTE_PREFIX + encodeURIComponent(task) + ROUTE_SUFFIX;
    const replay = REPLAY_ROUTE + "?" + target.key + "=" + encodeURIComponent(target.value);
    const [historyResult, replayResult, validateResult] = await Promise.all([
      read(route, task, "history"),
      read(replay, task, "replay"),
      read(VALIDATE_ROUTE, task, "validation"),
    ]);
    const state = combine([historyResult, replayResult, validateResult]);
    const validation = validateResult.body && validateResult.body.validation_status ? validateResult.body.validation_status : state;
    const fresh = historyResult.fresh !== "not returned" ? historyResult.fresh : (replayResult.fresh !== "not returned" ? replayResult.fresh : validateResult.fresh);
    const detail = state === "healthy"
      ? `authoritative success; metadata only for history/replay identifiers.`
      : `${state} history/replay response; metadata only; not authoritative.`;
    render(state, state === "healthy" ? "authoritative" : "non-authoritative", detail, task, target.text, `GET ${route}; GET ${replay}; GET ${VALIDATE_ROUTE}`, fresh, historyResult.count, replayResult.count, validation, linked(historyResult.body, replayResult.body, validateResult.body));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadHistoryReplay);
  } else {
    loadHistoryReplay();
  }
})();
