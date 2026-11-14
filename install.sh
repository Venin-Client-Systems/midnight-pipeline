#!/bin/bash
# Midnight installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Venin-Client-Systems/midnight-pipeline/main/install.sh | bash
#
# Clones Midnight into ~/Developer/midnight (or $MIDNIGHT_HOME) and runs
# `midnight setup`. It never uses sudo. Arguments are passed to setup:
#
#   curl -fsSL .../install.sh | bash -s -- --model qwen3-coder:30b --every-day
set -euo pipefail

REPOSITORY="${MIDNIGHT_REPOSITORY:-https://github.com/Venin-Client-Systems/midnight-pipeline.git}"
BRANCH="${MIDNIGHT_BRANCH:-main}"
TARGET="${MIDNIGHT_HOME:-$HOME/Developer/midnight}"
PYTHON=/opt/homebrew/bin/python3

fail() { printf '\nMidnight install stopped: %s\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "Midnight runs on macOS (it uses the macOS sandbox and launchd)."
[ "$(uname -m)" = "arm64" ] || fail "Midnight needs an Apple Silicon Mac."
command -v git >/dev/null 2>&1 || fail "Git is missing. Run: xcode-select --install"
[ -x "$PYTHON" ] || fail "Homebrew Python is missing. Install Homebrew from https://brew.sh, then run: brew install python"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || fail "Python 3.12 or later is needed. Run: brew upgrade python"

if [ -d "$TARGET/.git" ]; then
  printf 'Updating Midnight in %s\n' "$TARGET"
  git -C "$TARGET" pull --ff-only --quiet || fail "Could not update $TARGET. It may have local changes; see: git -C $TARGET status"
elif [ -e "$TARGET" ]; then
  fail "$TARGET exists and is not a Midnight checkout. Move it, or set MIDNIGHT_HOME to another folder."
else
  printf 'Installing Midnight into %s\n' "$TARGET"
  mkdir -p "$(dirname "$TARGET")"
  git clone --quiet --branch "$BRANCH" "$REPOSITORY" "$TARGET"
fi

exec "$PYTHON" "$TARGET/midnight.py" setup "$@"
