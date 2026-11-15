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

INSTALL_COMMAND='curl -fsSL https://raw.githubusercontent.com/Venin-Client-Systems/midnight-pipeline/main/install.sh | bash'
BREW=/opt/homebrew/bin/brew

fail() { printf '\nMidnight install stopped: %s\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "Midnight runs on macOS (it uses the macOS sandbox and launchd)."
[ "$(uname -m)" = "arm64" ] || fail "Midnight needs an Apple Silicon Mac (M1 or later)."

# Pick a model that fits this Mac's memory.
MEMORY_GB=$(( $(/usr/sbin/sysctl -n hw.memsize) / 1073741824 ))
if [ "$MEMORY_GB" -ge 32 ]; then MODEL=qwen3-coder:30b; MODEL_SIZE="19 GB"
elif [ "$MEMORY_GB" -ge 16 ]; then MODEL=qwen2.5-coder:14b; MODEL_SIZE="9 GB"
else MODEL=qwen2.5-coder:7b; MODEL_SIZE="5 GB"; fi

# Collect everything that is missing, so it can be shown as one list.
STEPS=()
step() { STEPS+=("$1"); }

xcode-select -p >/dev/null 2>&1 || step "Install Apple's developer tools (this is where Git comes from).
     A window opens: click Install and wait until it says it is done.

       xcode-select --install"

[ -x "$BREW" ] || step "Install Homebrew, the tool that installs the rest. It asks for your Mac password
     (nothing shows while you type) and takes a few minutes.

       /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""

if [ ! -x "$PYTHON" ]; then
  step "Install Python.

       $BREW install python"
elif ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
  step "Update Python (Midnight needs 3.12 or later).

       $BREW upgrade python"
fi

OLLAMA=$(command -v ollama || true)
[ -n "$OLLAMA" ] || { [ -x /opt/homebrew/bin/ollama ] && OLLAMA=/opt/homebrew/bin/ollama; } || true
if [ -z "$OLLAMA" ] && [ ! -d /Applications/Ollama.app ]; then
  step "Install Ollama, the program that runs the AI model on your Mac.

       $BREW install ollama"
  OLLAMA=/opt/homebrew/bin/ollama
fi
OLLAMA=${OLLAMA:-ollama}

MODELS=""
if curl -fs -m 3 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  MODELS=$(curl -fs -m 5 http://127.0.0.1:11434/api/tags 2>/dev/null | grep -o '"name"' | head -1 || true)
elif [ -d /Applications/Ollama.app ]; then
  step "Start Ollama.

       open -a Ollama"
else
  step "Start Ollama. It then starts by itself every time you log in.

       $BREW services start ollama"
fi

[ -n "$MODELS" ] || step "Download a coding model. Your Mac has $MEMORY_GB GB of memory, so this one fits.
     It is about $MODEL_SIZE and can take a while.

       $OLLAMA pull $MODEL"

if [ "${#STEPS[@]}" -gt 0 ]; then
  printf '\nBefore Midnight can install, this Mac needs the following. Paste each command below into\nthis Terminal window, in order, and wait for each one to finish.\n'
  NUMBER=1
  for STEP in "${STEPS[@]}"; do printf '\n  %s. %s\n' "$NUMBER" "$STEP"; NUMBER=$((NUMBER + 1)); done
  printf '\nWhen they are all done, run the install command again:\n\n       %s\n\n' "$INSTALL_COMMAND"
  exit 1
fi

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
