"""Fixture metadata for imports/violations — all files must produce violations."""

META = {
    "cross_service.py": {"owner": ("service", "registry-api"), "expect_violation": True},
    "metrics_subscriber_imports_service.py": {
        "owner": ("service", "metrics-subscriber"),
        "expect_violation": True,
    },
    "mcp_imports_service.py": {"owner": ("mcp-server", "clawhip-bridge"), "expect_violation": True},
    "package_imports_service.py": {"owner": ("package", "events"), "expect_violation": True},
}
