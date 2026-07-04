(function () {
  "use strict";

  const ROUTE = "/v1/events/replay/snapshots";
  const ROUTE_LABEL = "GET snapshot list";
  const CREATE_ROUTE = "/v1/events/replay/snapshots";
  const CREATE_ROUTE_LABEL = "POST snapshot create";
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

  function readFailureStatus(status) {
    return status === 401 || status === 403 ? "unauthorized" : "backend unavailable";
  }

  let createInFlight = false;

  function validCreatedSnapshot(row) {
    return validSnapshot(row);
  }

  function renderCreateStatus(status, detail) {
    text("lifecycle-snapshot-create-status", "Snapshot creation status: " + status + "; " + detail);
  }

  function renderCreateResult(row) {
    text(
      "lifecycle-snapshot-create-result",
      "Snapshot creation result: snapshot_id=" +
        row.snapshot_id +
        " sequence_number=" +
        row.sequence_number +
        " timestamp=" +
        row.timestamp +
        " size_bytes=" +
        row.size_bytes +
        "; metadata only; source=" +
        CREATE_ROUTE_LABEL +
        "; authorization source=existing bearer token."
    );
  }

  function bearerTokenFromInput() {
    const input = document.getElementById("lifecycle-snapshot-create-token");
    if (!input || typeof input.value !== "string") {
      return "";
    }
    return input.value.trim();
  }

  function createButton() {
    return document.getElementById("lifecycle-snapshot-create-button");
  }

  async function createLifecycleSnapshot(event) {
    if (event && typeof event.preventDefault === "function") {
      event.preventDefault();
    }
    if (createInFlight) {
      renderCreateStatus("in-flight", "duplicate submit blocked; non-authoritative until the current request completes.");
      return;
    }
    const token = bearerTokenFromInput();
    if (!token.startsWith("Bearer ") || token.length <= "Bearer ".length) {
      renderCreateStatus("authorization required", "non-authoritative; enter an existing bearer token before creating a snapshot.");
      return;
    }
    const button = createButton();
    createInFlight = true;
    if (button) {
      button.disabled = true;
    }
    renderCreateStatus("in-flight", "visible operator request submitted; no automatic repeat request will be attempted.");
    try {
      const response = await fetch(CREATE_ROUTE, {
        method: "POST",
        headers: { Authorization: token },
      });
      if (!response.ok) {
        renderCreateStatus(
          response.status === 401 || response.status === 403 ? "unauthorized" : "backend unavailable",
          "non-authoritative; snapshot create failed closed and will not be repeated automatically."
        );
        return;
      }
      if (response.status !== 201) {
        renderCreateStatus(
          "unexpected status",
          "non-authoritative; snapshot create did not return HTTP 201 and will not be repeated automatically."
        );
        return;
      }
      let payload;
      try {
        payload = await response.json();
      } catch (_error) {
        renderCreateStatus("invalid", "non-authoritative; invalid creation response and no automatic repeat request.");
        return;
      }
      if (!validCreatedSnapshot(payload)) {
        renderCreateStatus("invalid", "non-authoritative; malformed creation metadata and no automatic repeat request.");
        return;
      }
      renderCreateStatus("created", "authoritative POST 201 metadata returned for this visible operator action only.");
      renderCreateResult(payload);
    } catch (error) {
      const message = error && typeof error.message === "string" ? error.message.toLowerCase() : "";
      const status = message.indexOf("timeout") >= 0 ? "unknown outcome" : "backend unavailable";
      renderCreateStatus(status, "non-authoritative; no automatic repeat request after network, timeout, or unknown result.");
    } finally {
      createInFlight = false;
      if (button) {
        button.disabled = false;
      }
    }
  }

  function wireSnapshotCreate() {
    const button = createButton();
    if (button && typeof button.addEventListener === "function") {
      button.addEventListener("click", createLifecycleSnapshot);
    }
  }

  async function loadLifecycleSnapshot() {
    try {
      const response = await fetch(ROUTE, { method: "GET" });
      if (!response.ok) {
        renderFailure(readFailureStatus(response.status));
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
    document.addEventListener("DOMContentLoaded", function () {
      wireSnapshotCreate();
      loadLifecycleSnapshot();
    });
  } else {
    wireSnapshotCreate();
    loadLifecycleSnapshot();
  }
})();
