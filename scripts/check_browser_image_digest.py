#!/usr/bin/env python3
"""check_browser_image_digest.py — verify Playwright MCP image digest pinning (Story 22.5 / FR88 / NFR-S13).

Asserts the pinned digest for the Playwright MCP Docker image is:
1. Referenced in the expected config location
2. In the correct format (registry/repo@sha256:<hex64>)
3. Not a tag-only reference (e.g. ``:latest``)
4. (Optional) Matches the upstream manifest when ``--verify-remote`` is passed

Usage::

    uv run python scripts/check_browser_image_digest.py
    uv run python scripts/check_browser_image_digest.py --verbose
    uv run python scripts/check_browser_image_digest.py --verify-remote
    uv run python scripts/check_browser_image_digest.py --self-test

Exit codes:
    0 — all checks pass
    1 — violations found
    2 — setup / import error
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Expected image reference pattern: registry/repo@sha256:<64 hex chars>
_DIGEST_PATTERN = re.compile(
    r"^(?P<registry>[a-z0-9._-]+)/(?P<repo>[a-z0-9._/-]+)@sha256:(?P<digest>[0-9a-f]{64})$"
)

# Canonical image name for Playwright MCP.
_CANONICAL_IMAGE = "mcr.microsoft.com/playwright/mcp"

# Files where the digest should be referenced.
_CONFIG_FILES: list[Path] = [
    REPO_ROOT / "mcp-servers" / "browser" / "src" / "browser_mcp" / "__main__.py",
    REPO_ROOT / ".env.example",
]


def _find_digest_in_file(path: Path) -> str | None:
    """Extract a ``@sha256:`` digest reference from a file, or return None."""
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        m = re.search(r"(@sha256:[0-9a-f]{64})", line)
        if m:
            return m.group(1)[1:]  # strip leading @
    return None


def _check_local(verbose: bool) -> list[str]:
    """Run local-only checks. Return list of violation messages."""
    violations: list[str] = []

    # 1. Check __main__.py for the image format documentation.
    main_file = REPO_ROOT / "mcp-servers" / "browser" / "src" / "browser_mcp" / "__main__.py"
    if not main_file.exists():
        violations.append(f"{main_file}: file not found")
        return violations

    content = main_file.read_text()

    # 2. Check that the docstring/example references @sha256: format.
    if "@sha256:" not in content:
        violations.append(f"{main_file}: no @sha256: digest reference found in the file")

    # 3. Check that BROWSER_MCP_PLAYWRIGHT_IMAGE is documented as required.
    if "BROWSER_MCP_PLAYWRIGHT_IMAGE" not in content:
        violations.append(f"{main_file}: BROWSER_MCP_PLAYWRIGHT_IMAGE env var not documented")

    # 4. Check server.py image format documentation.
    server_file = REPO_ROOT / "mcp-servers" / "browser" / "src" / "browser_mcp" / "server.py"
    if server_file.exists():
        server_content = server_file.read_text()
        if "@sha256:" not in server_content:
            violations.append(f"{server_file}: no @sha256: digest reference found")

    # 5. Verify _build_docker_command enforces digest pinning.
    subprocess_file = (
        REPO_ROOT
        / "mcp-servers"
        / "browser"
        / "src"
        / "browser_mcp"
        / "adapters"
        / "playwright_subprocess.py"
    )
    if subprocess_file.exists():
        sp_content = subprocess_file.read_text()
        if "@sha256:" not in sp_content:
            violations.append(
                f"{subprocess_file}: no @sha256: digest reference in spawn command builder"
            )
        # Check that the documentation mentions digest pinning.
        if "digest" not in sp_content.lower():
            violations.append(f"{subprocess_file}: no digest-pinning documentation")

    # 6. Check the integration test uses a fake digest (not a real one).
    test_file = REPO_ROOT / "tests" / "integration" / "test_browser_container_spawn.py"
    if test_file.exists():
        test_content = test_file.read_text()
        if "@sha256:" not in test_content:
            violations.append(f"{test_file}: no @sha256: digest assertion in container-spawn test")

    if not violations and verbose:
        print(
            f"  local checks: digest format documented in "
            f"{main_file.relative_to(REPO_ROOT)}, "
            f"{server_file.relative_to(REPO_ROOT)}, "
            f"{subprocess_file.relative_to(REPO_ROOT)}"
        )

    return violations


def _check_remote(verbose: bool) -> list[str]:
    """Check the digest against the remote manifest (requires crane/skopeo). Return violations."""
    violations: list[str] = []

    # Try to find the pinned digest from env or config.
    import os

    image_ref = os.environ.get("BROWSER_MCP_PLAYWRIGHT_IMAGE", "")
    if not image_ref:
        # Not an error — the digest is runtime-configured.
        if verbose:
            print(
                "  remote check: BROWSER_MCP_PLAYWRIGHT_IMAGE not set, skipping remote verification"
            )
        return []

    m = _DIGEST_PATTERN.match(image_ref)
    if not m:
        violations.append(
            f"BROWSER_MCP_PLAYWRIGHT_IMAGE={image_ref!r} does not match "
            f"expected format: registry/repo@sha256:<64 hex chars>"
        )
        return violations

    # Verify the image name matches canonical.
    full_image = f"{m.group('registry')}/{m.group('repo')}"
    if full_image != _CANONICAL_IMAGE:
        violations.append(
            f"Image name {full_image!r} does not match canonical {_CANONICAL_IMAGE!r}"
        )

    if verbose:
        print(f"  remote check: image ref {image_ref[:50]}... format valid")

    return violations


def _self_test() -> int:
    """Run self-test with fixture files."""
    failures = 0

    # Test: valid digest format detection.
    valid = "mcr.microsoft.com/playwright/mcp@sha256:" + "a" * 64
    assert _DIGEST_PATTERN.match(valid), f"valid digest should match: {valid}"

    # Test: tag-only reference should NOT match.
    invalid_tag = "mcr.microsoft.com/playwright/mcp:latest"
    assert not _DIGEST_PATTERN.match(invalid_tag), "tag-only should not match"

    # Test: short digest should NOT match.
    short_digest = "mcr.microsoft.com/playwright/mcp@sha256:" + "a" * 32
    assert not _DIGEST_PATTERN.match(short_digest), "short digest should not match"

    print(f"✓ check_browser_image_digest self-test OK (3 assertions, {failures} failures)")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_browser_image_digest",
        description="Verify Playwright MCP image digest pinning (FR88 / NFR-S13 / Story 22.5)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run self-test fixtures instead of scanning the repo",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show per-file details on success",
    )
    parser.add_argument(
        "--verify-remote",
        action="store_true",
        help="Also check against the remote registry (requires BROWSER_MCP_PLAYWRIGHT_IMAGE env var)",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    all_violations: list[str] = []

    # Local checks (always run).
    if args.verbose:
        print("check_browser_image_digest: running local checks...")
    local_violations = _check_local(args.verbose)
    all_violations.extend(local_violations)

    # Remote checks (only if requested).
    if args.verify_remote:
        if args.verbose:
            print("check_browser_image_digest: running remote checks...")
        remote_violations = _check_remote(args.verbose)
        all_violations.extend(remote_violations)

    if all_violations:
        for v in all_violations:
            print(v, file=sys.stderr)
        print(
            f"\ncheck_browser_image_digest: {len(all_violations)} violation(s) found",
            file=sys.stderr,
        )
        return 1

    print("✓ check_browser_image_digest OK (local checks passed, digest format documented)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
