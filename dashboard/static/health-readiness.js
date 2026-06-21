(() => {
  "use strict";

  const ROUTE = "/v1/health";
  const JSON_ACCEPT = "application/json";
  const TARGETS = {
    status: "health-readiness-status",
    source: "health-readiness-source",
    freshness: "health-readiness-freshness",
    authority: "health-readiness-authority",
    detail: "health-readiness-detail",
  };

  function write(id, value) {
    const target = document.getElementById(id);
    if (target) {
      target.textContent = value;
    }
  }

  function render(view) {
    write(TARGETS.status, view.status);
    write(TARGETS.source, `Source route: GET ${ROUTE}.`);
    write(TARGETS.freshness, view.freshness);
    write(TARGETS.authority, view.authority);
    write(TARGETS.detail, view.detail);
  }

  function nowLabel() {
    return new Date(Date.now()).toISOString();
  }

  function nonAuthoritative(state, detail) {
    return {
      status: `Health readiness ${state}: non-authoritative read state.`,
      freshness: `Retrieved-at: ${nowLabel()}. Freshness: ${state}.`,
      authority: "Authority: non-authoritative.",
      detail,
    };
  }

  function viewFromBody(body) {
    if (!body || typeof body !== "object") {
      return nonAuthoritative("invalid", "Invalid health payload; bounded read copy is shown.");
    }

    const registry = String(body.registry_status || "unknown").toLowerCase();
    const worker = String(body.worker_status || "unknown").toLowerCase();
    const queueDepth = Number.isInteger(body.clawhip_queue_depth)
      ? body.clawhip_queue_depth
      : "unknown";
    const version = String(body.version || "unknown");

    if (registry === "ok" && worker === "ok") {
      return {
        status: "Health readiness healthy: authoritative success for the approved read.",
        freshness: `Retrieved-at: ${nowLabel()}. Freshness: healthy.`,
        authority: "Authority: authoritative.",
        detail: `registry=${registry}; worker=${worker}; clawhip_queue_depth=${queueDepth}; version=${version}.`,
      };
    }

    if (registry === "ok" && worker === "idle") {
      return nonAuthoritative(
        "stale",
        `Registry is ok but worker is idle; queue depth ${queueDepth}; version ${version}.`,
      );
    }

    if (registry === "degraded" || worker === "unknown") {
      return nonAuthoritative(
        "backend unavailable",
        `Health backend unavailable or degraded; registry=${registry}; worker=${worker}; version=${version}.`,
      );
    }

    return nonAuthoritative(
      "unavailable",
      `Health response is unavailable for authoritative display; registry=${registry}; worker=${worker}; version=${version}.`,
    );
  }

  async function loadHealthReadiness() {
    render(nonAuthoritative("loading", "Loading the approved health read."));
    try {
      const response = await fetch("/v1/health", {
        method: "GET",
        headers: { Accept: JSON_ACCEPT },
      });
      if (response.status === 401 || response.status === 403) {
        render(nonAuthoritative("unauthorized", "Unauthorized or forbidden health read."));
        return;
      }
      if (!response.ok) {
        render(nonAuthoritative("unavailable", `Health read returned HTTP ${response.status}.`));
        return;
      }
      let body;
      try {
        body = await response.json();
      } catch (_error) {
        render(nonAuthoritative("invalid", "Invalid health JSON; bounded read copy is shown."));
        return;
      }
      render(viewFromBody(body));
    } catch (_error) {
      render(nonAuthoritative("backend unavailable", "Health backend unavailable or network read failed."));
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadHealthReadiness, { once: true });
  } else {
    void loadHealthReadiness();
  }
})();
