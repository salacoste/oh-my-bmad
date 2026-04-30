"""Fixture metadata for imports/clean — all files must produce zero violations."""

META = {
    "pkg_imports_pkg.py": {"owner": ("package", "secret-hygiene"), "expect_violation": False},
    "service_imports_pkg.py": {"owner": ("service", "registry-api"), "expect_violation": False},
    "mcp_imports_pkg.py": {"owner": ("mcp-server", "task-registry"), "expect_violation": False},
    # Story 3.8 review H9: exercises the multi-tag noqa suppression path
    # (`# noqa: PLC0415, IMP001 — reason` on the offending line). Without
    # the H9 fix the IMP001 tag would have been silently ignored and this
    # fixture would have surfaced a violation.
    "multi_tag_noqa_service.py": {
        "owner": ("service", "registry-api"),
        "expect_violation": False,
    },
}
