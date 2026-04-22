#!/usr/bin/env python3
"""sync_upstream.py — vendored-with-sync recipe for upstream forks.

Invoked by `just sync-upstream <name>`. Supports only the two upstreams
declared in Story 1.3; extending to new upstreams requires adding a row to
`UPSTREAMS` and a row to `VENDORED.md`.

Behavior
--------
1. Validate `<name>` is one of {omc, clawhip}.
2. `git clone --depth 1` the upstream into a temp dir.
3. Read HEAD SHA.
4. rsync the content (excluding `.git/`) into `upstream/<name>/`, replacing
   any existing content including placeholder READMEs.
5. Rewrite the matching row in `VENDORED.md` with the new SHA + UTC date.
6. On network/fetch failure: leave `upstream/<name>/` untouched and exit
   non-zero with a clear error (protects the operator from a half-sync).

Usage
-----
    uv run python scripts/sync_upstream.py omc
    uv run python scripts/sync_upstream.py clawhip

Or via the wrapper recipe:
    just sync-upstream omc
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

UPSTREAMS = {
    "omc": "https://github.com/Yeachan-Heo/oh-my-claudecode",
    "clawhip": "https://github.com/Yeachan-Heo/clawhip",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED_MD = REPO_ROOT / "VENDORED.md"


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        die(f"usage: sync_upstream.py <name>   (name ∈ {{{', '.join(sorted(UPSTREAMS))}}})")
    name = argv[1]
    if name not in UPSTREAMS:
        die(f"unknown upstream {name!r}; supported: {', '.join(sorted(UPSTREAMS))}")

    url = UPSTREAMS[name]
    dest = REPO_ROOT / "upstream" / name

    if not dest.parent.is_dir():
        die(f"missing directory: {dest.parent} — run story 1.3 scaffold first")

    with tempfile.TemporaryDirectory(prefix=f"sync-upstream-{name}-") as tmp:
        tmp_path = Path(tmp)
        clone_target = tmp_path / name
        print(f"→ fetching {url} (depth=1) into {clone_target}")
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(clone_target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            preserved = "preserved existing content" if dest.exists() and any(dest.iterdir()) else "no existing content to preserve"
            msg = (
                f"git clone failed for {name} ({url}); {preserved}.\n"
                f"  stderr: {result.stderr.strip()}"
            )
            die(msg, code=2)

        sha_result = subprocess.run(
            ["git", "-C", str(clone_target), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        sha = sha_result.stdout.strip()
        print(f"→ HEAD SHA: {sha}")

        if dest.exists():
            print(f"→ clearing existing {dest}")
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)

        for child in clone_target.iterdir():
            if child.name == ".git":
                continue
            target = dest / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
        print(f"→ copied upstream content into {dest}")

    sync_date = datetime.now(UTC).strftime("%Y-%m-%d")
    update_vendored_md(name, url, sha, sync_date)
    print(f"→ updated {VENDORED_MD.name}: {name} @ {sha[:12]} ({sync_date})")
    print(f"✓ sync-upstream {name} complete")
    return 0


def update_vendored_md(name: str, url: str, sha: str, sync_date: str) -> None:
    if not VENDORED_MD.is_file():
        die(f"{VENDORED_MD} missing")

    text = VENDORED_MD.read_text()
    # Row format:
    # | `<name>` | <url> | <sha> | <date> ... | <first-use> |
    row_pattern = re.compile(
        r"^\| `" + re.escape(name) + r"` \| " + re.escape(url) + r" \| [^|]+ \| [^|]+ \|([^|\n]*)\|",
        re.MULTILINE,
    )
    match = row_pattern.search(text)
    if not match:
        die(f"VENDORED.md: could not find row for {name!r}; manual fix required")

    new_row = f"| `{name}` | {url} | `{sha}` | {sync_date} |{match.group(1)}|"
    new_text = text[: match.start()] + new_row + text[match.end() :]
    VENDORED_MD.write_text(new_text)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
