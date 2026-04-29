"""telegram-gateway aiogram message/callback handlers (Story 3.3+).

Story 3.3 ships the ``/task`` command handler (Bootstrap Minimum #1).
Future handlers:
- Story 3.4: ``/approve``
- Story 3.5: ``/ping``
- Stories 3.14–3.19: status / logs / stop / reject / retry / agent
"""

from telegram_gateway.handlers.task_command import make_task_router

__all__ = ["make_task_router"]
