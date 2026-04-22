#!/usr/bin/env bash
set -euo pipefail

CHANNEL_ARGS=()
if [[ -n "${CLAWHIP_CHANNEL:-}" ]]; then
  CHANNEL_ARGS=(--channel "$CLAWHIP_CHANNEL")
fi

repo=$(basename "$(git rev-parse --show-toplevel)")
branch=$(git rev-parse --abbrev-ref HEAD)
commit=$(git rev-parse HEAD)
summary=$(git log -1 --pretty=%s)

exec clawhip git commit \
  --repo "$repo" \
  --branch "$branch" \
  --commit "$commit" \
  --summary "$summary" \
  "${CHANNEL_ARGS[@]}"
