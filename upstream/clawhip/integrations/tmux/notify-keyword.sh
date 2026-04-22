#!/usr/bin/env bash
set -euo pipefail

session=${1:?usage: notify-keyword.sh <session> <keyword> <line> [channel]}
keyword=${2:?usage: notify-keyword.sh <session> <keyword> <line> [channel]}
line=${3:?usage: notify-keyword.sh <session> <keyword> <line> [channel]}
channel=${4:-${CLAWHIP_CHANNEL:-}}

CHANNEL_ARGS=()
if [[ -n "$channel" ]]; then
  CHANNEL_ARGS=(--channel "$channel")
fi

exec clawhip tmux keyword \
  --session "$session" \
  --keyword "$keyword" \
  --line "$line" \
  "${CHANNEL_ARGS[@]}"
