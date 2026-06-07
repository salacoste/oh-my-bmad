"""worker-wrapper adapter layer — external process / API boundaries."""

from worker_wrapper.adapters.approval_waiter import ApprovalResult, ApprovalWaiter
from worker_wrapper.adapters.claude_code_runner import (
    ClaudeCodeResult,
    ClaudeCodeRunner,
    ExtractedEvent,
    ReasoningBreadcrumb,
)
from worker_wrapper.adapters.gemini_runner import (
    GeminiResult,
    GeminiRunner,
)
from worker_wrapper.adapters.github_client import (
    BranchResult,
    GitHubClient,
    PRDraftResult,
)
from worker_wrapper.adapters.lifecycle_manager import LifecycleManager
from worker_wrapper.adapters.mcp_clients import (
    MCPClientGroup,
    verify_connectivity,
)
from worker_wrapper.adapters.runtime_factory import (
    SUPPORTED_RUNTIMES,
    get_runtime_adapter,
)

__all__ = [
    "ApprovalResult",
    "ApprovalWaiter",
    "BranchResult",
    "ClaudeCodeResult",
    "ClaudeCodeRunner",
    "ExtractedEvent",
    "GeminiResult",
    "GeminiRunner",
    "GitHubClient",
    "LifecycleManager",
    "MCPClientGroup",
    "PRDraftResult",
    "ReasoningBreadcrumb",
    "SUPPORTED_RUNTIMES",
    "get_runtime_adapter",
    "verify_connectivity",
]
