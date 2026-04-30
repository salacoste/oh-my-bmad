"""telegram-gateway production entry point (Story 3.1 AC-3).

Reads env vars, constructs :class:`SystemClock`, calls
:py:meth:`TelegramSettings.from_env` with a sentinel emit-disallowed
callable to obtain a placeholder-wrapped settings instance for the app
factory (the lifespan rewraps the secrets with the real ``emit``
callback once the audit writer is up — see
:mod:`telegram_gateway.app.lifespan`), then runs via
``uvicorn.run`` (programmatic, not CLI subprocess).

Logging stack (Story 3.6 AC-4 — supersedes review-fix M20)
----------------------------------------------------------

Story 3.6 wires structlog here as a JSON renderer plus the
``redact_secrets`` log-sanitizer processor (NFR-S1). Stdlib
``logging.getLogger(...)`` callers — which Story 2.16's ``audited_secret``
module relies on so pytest's ``caplog`` fixture continues to capture
WARN/ERROR records — are bridged through ``structlog.stdlib.ProcessorFormatter``
so the unified processor chain runs for both flavours. ``caplog`` keeps
seeing records (it attaches its own handler at the root); we additionally
get JSON output + secret redaction on the stderr handler.

Configuration is gated by an idempotency sentinel and invoked inside
``main()`` (NOT at module import) so importing ``__main__`` for tests does
not mutate the root logger (review-fix M24).

Story 3.6 deviates from architecture.md:826 which says ``app/main.py`` —
keeping it at the entry-point avoids pytest fixture pollution; see story
Dev Notes.

Bootstrap-actor footgun (review-fix M26)
----------------------------------------

The placeholder ``from_env`` call passes ``emit=_bootstrap_emit_disallowed``
— a sentinel callable that raises ``RuntimeError`` if invoked. If any
code path between this entrypoint and the lifespan-rewrap accidentally
reads ``.value`` on the placeholder instance, the bug surfaces
immediately rather than silently producing audit events under the
wrong actor identity.

Environment variables consumed by :class:`TelegramSettings`:

* ``TELEGRAM_BOT_TOKEN`` (required, audited)
* ``TELEGRAM_WEBHOOK_SECRET_TOKEN`` (required, audited)
* ``TELEGRAM_WEBHOOK_URL`` (required, must be ``https``)
* ``TELEGRAM_WEBHOOK_PATH`` (optional; default ``/v1/telegram/webhook``)
* ``EVENT_LOG_DIR`` (optional; default ``/var/lib/oh-my-bmad/events``)

Plus host/port for uvicorn:

* ``TELEGRAM_GATEWAY_HOST`` (default ``127.0.0.1`` — review-fix H7).
  Containers + docker-compose explicitly set ``0.0.0.0``; the local
  default is loopback-only so a workstation run does not expose the
  service to LAN.
* ``TELEGRAM_GATEWAY_PORT`` (default ``8080``)
"""

from __future__ import annotations

import logging
import os
import sys
from typing import NoReturn

import structlog
import uvicorn
from events.clock import SystemClock
from events.envelope import Actor, EventEnvelope
from secret_hygiene.sanitizer import redact_secrets

from telegram_gateway.app.config import TelegramSettings
from telegram_gateway.app.main import build_app

_SERVICE = "telegram-gateway"

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8080

# Story 3.6 AC-4: idempotency sentinel — re-running ``main()`` (e.g. across
# multiple test cases that import the module) must not double-wire the
# structlog processor chain or stack handlers on the root logger.
_STRUCTLOG_CONFIGURED: bool = False


def _configure_logging() -> None:
    """Wire structlog + bridge stdlib logging through the same processor chain.

    Idempotent: re-entry across ``main()`` calls is a no-op. Order of the
    pre-chain is load-bearing: ``redact_secrets`` MUST run before any
    rendering or the JSON output contains the unredacted secret bytes
    (architecture.md:417).
    """
    global _STRUCTLOG_CONFIGURED
    if _STRUCTLOG_CONFIGURED:
        return

    # Story 3.6 H1/H3: ``ExtraAdder`` promotes ``extra={...}`` kwargs from
    # stdlib ``logging`` calls into the structlog event_dict so the
    # downstream ``redact_secrets`` processor can value-pattern-redact
    # secrets that arrive via ``_log.warning(..., extra={...})``. Without
    # it, stdlib LogRecord extras would never be copied onto the
    # event_dict and a leaked secret would render unredacted.
    pre_chain: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_secrets,
    ]

    # Canonical structlog 24.x bridge pattern (Story 3.6 review H3): the
    # ``ProcessorFormatter`` runs ``foreign_pre_chain`` ONLY for foreign
    # (stdlib) records and the ``processors=[remove_processors_meta,
    # JSONRenderer()]`` list for both flavours. Native structlog records
    # already had ``pre_chain`` applied via ``structlog.configure(processors=
    # [*pre_chain, wrap_for_formatter])`` and arrive here flagged with
    # ``_from_structlog=True`` so the foreign pre-chain is correctly skipped
    # — each field appears EXACTLY ONCE in the JSON output regardless of
    # whether the caller used native ``structlog.get_logger(...)`` or stdlib
    # ``logging.getLogger(...)``.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _STRUCTLOG_CONFIGURED = True


# Bootstrap actor for the placeholder ``from_env`` call. Never produces
# audit events (emit is a raise-sentinel) but satisfies the API
# contract; the lifespan rebuilds with a real actor before any value
# read.
_BOOTSTRAP_ACTOR = Actor(kind="system", id="telegram-gateway-bootstrap")


async def _bootstrap_emit_disallowed(envelope: EventEnvelope) -> NoReturn:
    """Sentinel emit callable: raises if ever invoked (review-fix M26).

    The ``__main__`` entrypoint passes this as ``emit`` to the
    placeholder ``TelegramSettings.from_env(...)`` call. Any code path
    that reads ``.value`` on the placeholder instance — which would emit
    a ``secret.accessed`` envelope under the bootstrap actor instead of
    the real lifespan actor — fails loudly here instead of silently
    producing wrong-actor audit events.
    """
    raise RuntimeError(
        "telegram-gateway bootstrap settings should never emit audit "
        "events; .value reads must wait until the lifespan rewraps "
        f"the secret with the real EventLogWriter (envelope={envelope.event_id})"
    )


def main() -> None:
    """Read configuration from env, build the app, and start uvicorn."""
    # Story 3.6 AC-4: structlog + secret-redactor bridge replaces the
    # previous ``logging.basicConfig`` block. Configured INSIDE ``main`` for
    # the same reason as before (review-fix M24): module-level wiring at
    # import time mutates the root logger of any test that touches
    # ``telegram_gateway.__main__``.
    _configure_logging()
    log = logging.getLogger(_SERVICE)

    host = os.environ.get("TELEGRAM_GATEWAY_HOST", _DEFAULT_HOST)
    port_raw = os.environ.get("TELEGRAM_GATEWAY_PORT", str(_DEFAULT_PORT))
    try:
        port = int(port_raw)
    except ValueError:
        # Friendly fail-fast (review-fix M19) — bare ValueError traceback
        # buries the operator-facing root cause.
        log.error(
            "TELEGRAM_GATEWAY_PORT must be an integer (got %r); "
            "set a numeric port or unset to use default %d",
            port_raw,
            _DEFAULT_PORT,
        )
        sys.exit(2)

    # Loud warning if the operator has bound to all interfaces (review-fix
    # H7). Compose / container deployments deliberately set 0.0.0.0; on
    # a workstation it usually means the loopback default has been
    # overridden by accident.
    if host == "0.0.0.0":  # noqa: S104 — runtime warning, not a bind
        log.warning(
            "Listening on 0.0.0.0 — ensure tunnel/firewall is in front; "
            "on workstations prefer the default 127.0.0.1"
        )

    clock = SystemClock()

    # emit sentinel: the lifespan rebuilds the settings with the real
    # EventLogWriter.append once the writer exists. We need a populated
    # instance HERE so build_app's lifespan factory can read
    # ``event_log_dir`` / ``webhook_path`` before any audit fires.
    settings = TelegramSettings.from_env(
        emit=_bootstrap_emit_disallowed,
        actor=_BOOTSTRAP_ACTOR,
        clock=clock,
    )

    log.info(
        "%s starting — host=%s port=%d webhook_path=%s",
        _SERVICE,
        host,
        port,
        settings.webhook_path,
    )

    app = build_app(settings=settings, clock=clock)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
