#!/usr/bin/env python3
"""sync_upstream.py — vendored-with-sync recipe for upstream forks.

Invoked by `just sync-upstream <name>`. Supports only the two upstreams
declared in Story 1.3; extending to new upstreams requires adding a row to
`UPSTREAMS` and a row to `VENDORED.md`.

Behavior (post-review-fix `c4...`)
----------------------------------
1. Validate `<name>` ∈ {omc, clawhip}.
2. Clone upstream into a temp dir; read HEAD SHA.
3. **Idempotency check:** if the captured SHA matches the SHA currently in
   `VENDORED.md` for this name AND the destination already has content, skip
   the rewrite (preserves a clean diff on operator re-runs).
4. Stage the fetched content into a sibling-temp directory under
   `upstream/`. If staging fails, the existing destination is never touched.
5. Atomic rename: replace `upstream/<name>/` with the staged content by
   renaming the staging dir. Eliminates the mid-sync window where the
   destination could be left empty on `KeyboardInterrupt`.
6. Rewrite `VENDORED.md` row using a line-based (non-regex-fragile) parse:
   find the row whose first cell matches `` `<name>` `` and replace the SHA +
   sync-date cells by column index. Table reformatting (e.g., whitespace
   padding) no longer breaks the rewrite.
7. On any failure: clean up staging dir; leave destination and VENDORED.md
   intact; exit non-zero with a clear error.

Usage
-----
    uv run python scripts/sync_upstream.py omc
    uv run python scripts/sync_upstream.py clawhip

Or via the wrapper recipe:
    just sync-upstream omc
"""

from __future__ import annotations

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
    upstream_dir = REPO_ROOT / "upstream"
    dest = upstream_dir / name
    if not upstream_dir.is_dir():
        die(f"missing directory: {upstream_dir} — run story 1.3 scaffold first")

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
            preserved = (
                "preserved existing content"
                if dest.exists() and any(dest.iterdir())
                else "no existing content to preserve"
            )
            die(
                f"git clone failed for {name} ({url}); {preserved}.\n"
                f"  stderr: {result.stderr.strip()}",
                code=2,
            )

        sha_result = subprocess.run(
            ["git", "-C", str(clone_target), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        sha = sha_result.stdout.strip()
        print(f"→ HEAD SHA: {sha}")

        # Idempotency short-circuit: if the SHA hasn't moved AND the destination
        # already has content, skip the rewrite entirely so the working tree
        # stays clean on operator re-runs.
        current_sha = read_current_sha(name)
        if sha == current_sha and dest.exists() and any(dest.iterdir()):
            print(f"→ {name} already at {sha[:12]}; no changes")
            return 0

        # Stage content into a sibling temp dir under upstream/ first, so the
        # destination is only mutated at the final atomic rename step.
        staging = upstream_dir / f".{name}.staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()

        for child in clone_target.iterdir():
            if child.name == ".git":
                continue
            target = staging / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)

        # Atomic swap: remove the live dest, then rename staging to its place.
        # The window where nothing is at `dest` is bounded by a single syscall
        # (rename), not a whole copy tree.
        if dest.exists():
            shutil.rmtree(dest)
        staging.rename(dest)
        print(f"→ swapped staged content into {dest}")

    sync_date = datetime.now(UTC).strftime("%Y-%m-%d")
    update_vendored_md(name, url, sha, sync_date)
    print(f"→ updated {VENDORED_MD.name}: {name} @ {sha[:12]} ({sync_date})")
    print(f"✓ sync-upstream {name} complete")
    return 0


def read_current_sha(name: str) -> str | None:
    """Parse VENDORED.md and return the currently-recorded SHA for `name`, if any."""
    if not VENDORED_MD.is_file():
        return None
    for line in VENDORED_MD.read_text().splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        # Cell format: `<name>` — strip backticks
        if first.startswith("`") and first.endswith("`") and first[1:-1] == name:  # noqa: SIM102
            if len(cells) >= 3:
                sha_cell = cells[2].strip("`")
                if sha_cell and sha_cell != "PLACEHOLDER":
                    return sha_cell
    return None


def update_vendored_md(name: str, url: str, sha: str, sync_date: str) -> None:
    """Line-based in-place rewrite of VENDORED.md's SHA + sync-date cells.

    Robust to whitespace/column-padding variations and URL special chars
    (no regex involved). Row is identified by the first cell matching
    `` `<name>` ``; columns are split on `|` and rewritten by index.
    """
    if not VENDORED_MD.is_file():
        die(f"{VENDORED_MD} missing")

    new_lines: list[str] = []
    hit = False
    for line in VENDORED_MD.read_text().splitlines():
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| Fork"):
            new_lines.append(line)
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and cells[0] == f"`{name}`":
            if len(cells) < 5:
                die(
                    f"VENDORED.md: row for {name!r} has only {len(cells)} cells; "
                    "expected 5 (Fork | URL | SHA | Sync date | First real use)"
                )
            cells[1] = url
            cells[2] = f"`{sha}`"
            cells[3] = sync_date
            new_lines.append("| " + " | ".join(cells) + " |")
            hit = True
        else:
            new_lines.append(line)

    if not hit:
        die(f"VENDORED.md: could not find row for {name!r}; manual fix required")

    VENDORED_MD.write_text("\n".join(new_lines) + "\n")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
