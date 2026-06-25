(function () {
  "use strict";

  const ROUTE = "/v1/events/replay/snapshots";
  const ROUTE_LABEL = "GET snapshot list";
  const DISPLAY_STATES = {
    degraded: "degraded",
    empty: "empty",
    "empty successful read": "empty",
    healthy: "healthy",
    invalid: "invalid",
    stale: "stale",
    unavailable: "unavailable",
    "unavailable read": "unavailable read",
    unauthorized: "unauthorized",
    "backend unavailable": "backend unavailable",
    "route failure/read error": "route failure/read error",
  };
  const REQUIRED_EVIDENCE = [
    "plan_hash",
    "dry_run_artifact_ref",
    "safety_policy_version",
    "retention_input_digest",
    "affected_segments",
    "replay_validation_ref",
    "rollback_evidence_ref",
    "operator_identity",
    "authorized_at",
    "authorization_event_ref",
    "archive_manifest_ref",
    "archive_manifest_validation",
    "archive_error_boundary",
  ];

  function text(id, value) {
    const node = document.getElementById(id);
    if (node) {
      node.textContent = value;
    }
  }

  function hasText(value) {
    return typeof value === "string" && value.trim() !== "";
  }

  function lower(value) {
    return hasText(value) ? value.toLowerCase() : "";
  }

  function validSnapshot(row) {
    return (
      row &&
      hasText(row.snapshot_id) &&
      Number.isFinite(row.sequence_number) &&
      hasText(row.timestamp) &&
      Number.isFinite(row.size_bytes)
    );
  }

  function snapshotSummary(rows) {
    if (!rows.length) {
      return "Snapshot rows: empty successful read; metadata only.";
    }
    return rows
      .map(function (row) {
        return (
          "snapshot_id=" +
          row.snapshot_id +
          " sequence_number=" +
          row.sequence_number +
          " timestamp=" +
          row.timestamp +
          " size_bytes=" +
          row.size_bytes +
          " metadata only"
        );
      })
      .join("; ");
  }

  function evidenceFromGlobal() {
    if (typeof window !== "undefined" && window.LIFECYCLE_SNAPSHOT_EVIDENCE) {
      return window.LIFECYCLE_SNAPSHOT_EVIDENCE;
    }
    if (typeof LIFECYCLE_SNAPSHOT_EVIDENCE !== "undefined") {
      return LIFECYCLE_SNAPSHOT_EVIDENCE;
    }
    return {};
  }

  function evidenceState(evidence) {
    if (!evidence || typeof evidence !== "object") {
      return { status: "missing lifecycle evidence", authority: "non-authoritative" };
    }
    const replayRef = lower(evidence.replay_validation_ref);
    if (replayRef.indexOf("failed replay validation") >= 0) {
      return { status: "failed replay validation", authority: "non-authoritative" };
    }
    if (replayRef.indexOf("stale replay evidence") >= 0) {
      return { status: "stale replay evidence", authority: "non-authoritative" };
    }
    if (Object.prototype.hasOwnProperty.call(evidence, "rollback_evidence_ref") && !hasText(evidence.rollback_evidence_ref)) {
      return { status: "missing rollback evidence", authority: "non-authoritative" };
    }
    const manifest = lower(evidence.archive_manifest_validation);
    if (manifest.indexOf("invalid archive configuration") >= 0) {
      return { status: "invalid archive configuration", authority: "non-authoritative" };
    }
    const boundary = lower(evidence.archive_error_boundary);
    if (boundary.indexOf("unverifiable lifecycle evidence") >= 0) {
      return { status: "unverifiable lifecycle evidence", authority: "non-authoritative" };
    }
    for (const key of REQUIRED_EVIDENCE) {
      if (!hasText(evidence[key])) {
        return { status: "missing lifecycle evidence", authority: "non-authoritative" };
      }
    }
    if (manifest !== "valid archive manifest") {
      return { status: "invalid archive configuration", authority: "non-authoritative" };
    }
    return { status: "ready", authority: "authoritative" };
  }

  function bodyState(body) {
    if (!body || typeof body !== "object" || !Array.isArray(body.snapshots)) {
      return { status: "invalid", rows: [], freshness: "not returned", total: 0 };
    }
    const rows = body.snapshots;
    const total = Number.isFinite(body.total) ? body.total : rows.length;
    if (total !== rows.length || !rows.every(validSnapshot)) {
      return { status: "invalid", rows: [], freshness: hasText(body.retrieved_at) ? body.retrieved_at : "not returned", total: rows.length };
    }
    const display = lower(body.display_state);
    const freshnessState = lower(body.freshness_state);
    let status = rows.length === 0 ? "empty" : "healthy";
    if (display) {
      status = Object.prototype.hasOwnProperty.call(DISPLAY_STATES, display)
        ? DISPLAY_STATES[display]
        : "invalid";
    }
    if (freshnessState === "stale") {
      status = "stale";
    }
    return {
      status: status,
      rows: rows,
      freshness: hasText(body.retrieved_at) ? body.retrieved_at : "not returned",
      total: total,
    };
  }

  function render(result) {
    const evidence = evidenceState(evidenceFromGlobal());
    const body = result.body;
    const healthy = body.status === "healthy" && evidence.status === "ready";
    const status = healthy ? "healthy" : body.status !== "healthy" ? body.status : evidence.status;
    const authority = healthy ? "authoritative" : "non-authoritative";
    text("lifecycle-snapshot-status", "Lifecycle snapshot state: " + status + ".");
    text("lifecycle-snapshot-source", "Source: " + ROUTE_LABEL + ". Runtime route read once with GET.");
    text("lifecycle-snapshot-count", "Snapshots: " + body.total + ".");
    text("lifecycle-snapshot-freshness", "Freshness: " + body.freshness + ".");
    text("lifecycle-snapshot-authority", "Authority: " + authority + ".");
    text("lifecycle-snapshot-items", snapshotSummary(body.rows));
    text("lifecycle-snapshot-evidence", "Lifecycle evidence: " + evidence.status + ".");
    text("lifecycle-snapshot-degraded", "Degraded state: " + (healthy ? "none" : status) + ".");
    text(
      "lifecycle-snapshot-detail",
      "Detail: " + status + "; metadata only; not controls; route identifiers do not drive adjacent surfaces."
    );
  }

  function renderFailure(status) {
    const body = { status: status, rows: [], freshness: "not returned", total: 0 };
    render({ body: body });
  }

  async function loadLifecycleSnapshot() {
    try {
      const response = await fetch(ROUTE, { method: "GET" });
      if (!response.ok) {
        renderFailure(response.status === 401 || response.status === 403 ? "unauthorized" : "backend unavailable");
        return;
      }
      let payload;
      try {
        payload = await response.json();
      } catch (_error) {
        renderFailure("invalid");
        return;
      }
      render({ body: bodyState(payload) });
    } catch (_error) {
      renderFailure("backend unavailable");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadLifecycleSnapshot);
  } else {
    loadLifecycleSnapshot();
  }
})();
