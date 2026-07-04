(function () {
  "use strict";

  const ROUTE_PREFIX = "/v1/tasks/";
  const ROUTE_SUFFIX = "/logs/digest/stream";
  const ROUTE_PATTERN = "GET /v1/tasks/{task_id}/logs/digest/stream";
  const MAX_CHUNKS = 10;
  const MAX_CHUNK_LENGTH = 2000;
  const STREAM_TIMEOUT_MS = 15000;
  const ALLOWED_FRAME_KEYS = new Set([
    "type",
    "task_id",
    "route",
    "sequence",
    "chunk",
    "retrieved_at",
    "freshness_state",
    "display_state",
    "authority_state",
    "provenance",
    "request_id",
    "trace_id",
    "correlation_id",
    "truncated",
    "line_count",
    "chunk_count",
  ]);
  const ALLOWED_FRAME_TYPES = new Set(["open", "chunk", "final"]);
  const ALLOWED_FRESHNESS = new Set(["fresh", "stale"]);
  const ALLOWED_FINAL_DISPLAY_STATES = new Set(["healthy", "stale", "provider-unavailable", "backend-unavailable", "unauthorized", "invalid"]);
  function overbroadPattern(parts) {
    return new RegExp(parts.join(""), "i");
  }

  const OVERBROAD_PATTERNS = [
    overbroadPattern(["\\b(?:payload_json|provider_internal|an", "thropic|op", "enai|hrefs?|pro", "mpts?|urls?|source\\s+tokens?)\\b"]),
    overbroadPattern(["\\b(?:event\\s+payloads?|raw\\s+events?|raw\\s+logs?|provider\\s+internals?|control\\s+hints?)\\b"]),
    /\b(?:https?|file):\/\//i,
    overbroadPattern(["(?<!\\w)(?:re", "try|control)(?!\\w)"]),
    /(?:(?<=\s)|^|["'`<({\[])(?:~\/|\.{1,2}\/|\/(?:users|private|tmp|home|var|etc|opt|usr|root|volumes|workspace|workspaces|mnt)\/|[a-z]:[\\/])\S*/i,
  ];

  function element(id) {
    return document.getElementById(id);
  }

  function write(id, value) {
    const target = element(id);
    if (target) target.textContent = value;
  }

  function visibleTaskId() {
    const source = element("digest-stream-task-id-source");
    return source ? source.textContent.trim() : "";
  }

  function label(value, fallback) {
    if (typeof value !== "string") return fallback;
    const text = value.trim();
    return text || fallback;
  }

  function render(state, authority, detail, taskId, route, freshness, provenance, correlation, degraded) {
    write("digest-stream-status", `Digest stream state: ${state}.`);
    write("digest-stream-source", `Source: ${ROUTE_PATTERN}. Runtime route: ${route || ROUTE_PATTERN}.`);
    write("digest-stream-task-id", `task_id: ${taskId || "missing"}.`);
    write("digest-stream-freshness", `Freshness: ${freshness}.`);
    write("digest-stream-authority", `Authority: ${authority}.`);
    write("digest-stream-provenance", `Provenance: ${provenance}.`);
    write("digest-stream-correlation", `Correlation: ${correlation}.`);
    write("digest-stream-degraded", `Degraded state: ${degraded}.`);
    write("digest-stream-detail", `Stream: ${detail}`);
  }

  function failClosed(state, detail, taskId, route) {
    render(state, "non-authoritative", detail, taskId, route, "missing server freshness", "backend digest stream response", "not provided", state);
  }

  function readFailureState(status) {
    return status === 401 || status === 403 ? "unauthorized" : "backend-unavailable";
  }

  function hasOnlyAllowedKeys(frame) {
    return Object.keys(frame).every((key) => ALLOWED_FRAME_KEYS.has(key));
  }

  function containsOverbroadValue(value) {
    if (typeof value !== "string") return false;
    return OVERBROAD_PATTERNS.some((pattern) => pattern.test(value));
  }

  function validateFrame(frame, taskId) {
    if (!frame || typeof frame !== "object" || Array.isArray(frame)) return "malformed frame";
    if (!hasOnlyAllowedKeys(frame)) return "unexpected stream frame keys";
    if (!ALLOWED_FRAME_TYPES.has(frame.type)) return "unexpected stream frame type";
    if (frame.task_id !== taskId) return "mismatched task_id";
    if (frame.route !== ROUTE_PATTERN) return "mismatched source route";
    for (const value of Object.values(frame)) {
      if (containsOverbroadValue(value)) return "over-broad stream value";
    }
    if (frame.type === "chunk") {
      if (typeof frame.chunk !== "string" || !frame.chunk || frame.chunk.length > MAX_CHUNK_LENGTH) return "malformed chunk";
    }
    if (frame.type === "final") {
      if (!ALLOWED_FRESHNESS.has(label(frame.freshness_state, ""))) return "ambiguous freshness";
      if (!ALLOWED_FINAL_DISPLAY_STATES.has(label(frame.display_state, ""))) return "invalid final state";
      if (frame.display_state !== "healthy" && frame.authority_state === "authoritative") return "invalid degraded authority";
    }
    return "";
  }

  function validateFrameSequence(frames) {
    if (!Array.isArray(frames) || frames.length < 3) return "interrupted digest stream";
    const openFrames = frames.filter((frame) => frame.type === "open");
    const finalFrames = frames.filter((frame) => frame.type === "final");
    if (openFrames.length !== 1 || finalFrames.length !== 1) return "ambiguous stream frame envelope";
    if (frames[0].type !== "open" || frames[0].sequence !== 0) return "invalid open frame position";
    const final = frames[frames.length - 1];
    if (final.type !== "final") return "invalid final frame position";
    const chunks = frames.slice(1, -1);
    if (!chunks.length || chunks.length > MAX_CHUNKS) return "invalid chunk volume";
    for (let index = 0; index < chunks.length; index += 1) {
      const chunk = chunks[index];
      if (chunk.type !== "chunk") return "invalid chunk frame position";
      if (chunk.sequence !== index + 1) return "non-contiguous chunk sequence";
    }
    if (final.sequence !== chunks.length + 1) return "non-contiguous final sequence";
    if (final.chunk_count !== chunks.length) return "mismatched chunk count";
    if (!Number.isInteger(final.line_count) || final.line_count < 1 || final.line_count > 20) return "invalid line count";
    if (typeof final.truncated !== "boolean") return "invalid truncated marker";
    return "";
  }

  async function readFrames(response, taskId) {
    if (!response.body || !response.body.getReader) throw new Error("missing readable stream");
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffered = "";
    const frames = [];
    for (;;) {
      const read = await reader.read();
      if (read.done) break;
      buffered += decoder.decode(read.value, { stream: true });
      const lines = buffered.split("\n");
      buffered = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const frame = JSON.parse(line);
        const reason = validateFrame(frame, taskId);
        if (reason) throw new Error(reason);
        frames.push(frame);
        if (frame.type === "chunk" && frames.filter((item) => item.type === "chunk").length > MAX_CHUNKS) throw new Error("excessive chunk volume");
      }
    }
    buffered += decoder.decode();
    if (buffered.trim()) {
      const frame = JSON.parse(buffered);
      const reason = validateFrame(frame, taskId);
      if (reason) throw new Error(reason);
      frames.push(frame);
    }
    return frames;
  }

  function renderFrames(frames, taskId, route) {
    const sequenceError = validateFrameSequence(frames);
    if (sequenceError) {
      failClosed("invalid", `${sequenceError}; not authoritative.`, taskId, route);
      return;
    }
    const final = frames[frames.length - 1];
    const chunks = frames.slice(1, -1);
    const chunkText = chunks.map((frame) => frame.chunk).join(" ");
    if (final.freshness_state === "stale" || final.display_state === "stale") {
      render("stale", "non-authoritative", `stale digest stream; ${chunkText}; not authoritative.`, taskId, route, label(final.retrieved_at, "missing server freshness"), label(final.provenance, "backend digest stream response"), label(final.correlation_id, label(final.request_id, label(final.trace_id, "not provided"))), "stale");
      return;
    }
    if (final.display_state !== "healthy" || final.authority_state !== "authoritative") {
      const state = label(final.display_state, "invalid");
      render(state, "non-authoritative", `${state.replace(/-/g, " ")} digest stream; ${chunkText}; not authoritative.`, taskId, route, label(final.retrieved_at, "missing server freshness"), label(final.provenance, "backend digest stream response"), label(final.correlation_id, label(final.request_id, label(final.trace_id, "not provided"))), state);
      return;
    }
    render("healthy", "authoritative", `authoritative digest stream chunks: ${chunkText}`, taskId, route, label(final.retrieved_at, "missing server freshness"), label(final.provenance, "backend digest stream response"), label(final.correlation_id, label(final.request_id, label(final.trace_id, "not provided"))), "none");
  }

  async function loadDigestStream() {
    const taskId = visibleTaskId();
    if (!taskId) {
      failClosed("missing task_id", "missing task_id visible source; no stream fetch attempted.", "missing", ROUTE_PATTERN);
      return;
    }
    const route = ROUTE_PREFIX + encodeURIComponent(taskId) + ROUTE_SUFFIX;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), STREAM_TIMEOUT_MS);
    try {
      const response = await fetch(route, { method: "GET", signal: controller.signal });
      if (!response.ok) {
        const state = readFailureState(response.status);
        failClosed(state, `${state.replace(/-/g, " ")} digest stream response; not authoritative.`, taskId, `GET ${route}`);
        return;
      }
      const contentType = label(response.headers && response.headers.get ? response.headers.get("content-type") : "", "").toLowerCase();
      if (!contentType.startsWith("application/x-ndjson")) {
        failClosed("invalid", "invalid digest stream content type; not authoritative.", taskId, `GET ${route}`);
        return;
      }
      const frames = await readFrames(response, taskId);
      renderFrames(frames, taskId, `GET ${route}`);
    } catch (error) {
      const state = error && error.name === "AbortError" ? "backend-unavailable" : "invalid";
      failClosed(state, `${state} digest stream read error; not authoritative.`, taskId, `GET ${route}`);
    } finally {
      clearTimeout(timeoutId);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadDigestStream);
  } else {
    loadDigestStream();
  }
})();
