"""Fixture metadata for imports/clean — all files must produce zero violations."""

META = {
    "pkg_imports_pkg.py": {"owner": ("package", "secret-hygiene"), "expect_violation": False},
    "service_imports_pkg.py": {"owner": ("service", "registry-api"), "expect_violation": False},
    "mcp_imports_pkg.py": {"owner": ("mcp-server", "task-registry"), "expect_violation": False},
}
