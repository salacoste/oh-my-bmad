"""worker-wrapper adapter layer — external process / API boundaries."""

from worker_wrapper.adapters.approval_waiter import ApprovalResult, ApprovalWaiter
from worker_wrapper.adapters.claude_code_runner import (
    ClaudeCodeResult,
    ClaudeCodeRunner,
    ExtractedEvent,
    ReasoningBreadcrumb,
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

__all__ = [
    "ApprovalResult",
    "ApprovalWaiter",
    "BranchResult",
    "ClaudeCodeResult",
    "ClaudeCodeRunner",
    "ExtractedEvent",
    "GitHubClient",
    "LifecycleManager",
    "MCPClientGroup",
    "PRDraftResult",
    "ReasoningBreadcrumb",
    "verify_connectivity",
]
