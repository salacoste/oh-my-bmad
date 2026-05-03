"""telegram-gateway aiogram message/callback handlers (Story 3.3+).

Story 3.3 ships the ``/task`` command handler (Bootstrap Minimum #1).
Story 3.4 ships the ``/approve`` command handler (Bootstrap Minimum #2).
Story 3.5 ships the ``/ping`` command handler (Bootstrap Minimum #3 — closes Bootstrap Milestone).
Future handlers:
- Stories 3.14–3.19: status / logs / stop / reject / retry / agent
"""

from telegram_gateway.handlers.approve_command import make_approve_router
from telegram_gateway.handlers.ping_command import make_ping_router
from telegram_gateway.handlers.status_command import make_status_router
from telegram_gateway.handlers.task_command import make_task_router

# L3: HealthResponseLocal removed from public __all__ — it is a transport-internal
# model. Tests import it directly from telegram_gateway.handlers.registry_client.
__all__ = ["make_approve_router", "make_ping_router", "make_status_router", "make_task_router"]
