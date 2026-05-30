#!/usr/bin/env bash
# install.sh — set up the human-memory transparent shim.
#
# Creates ~/.agent-memory/{bin,lib,log}, installs the generic shim + watcher,
# symlinks the agent names to the shim, and prints the one PATH line to add to
# your shell rc. Idempotent: safe to re-run.

set -euo pipefail

AGENT_MEMORY_HOME="${AGENT_MEMORY_HOME:-$HOME/.agent-memory}"
src_dir="$(cd "$(dirname "$(realpath "$0")")" && pwd)"

# Agents to shim. Add a name here (and it'll be symlinked) to cover a new tool.
AGENTS=(claude codex opencode)

bin_dir="$AGENT_MEMORY_HOME/bin"
lib_dir="$AGENT_MEMORY_HOME/lib"
log_dir="$AGENT_MEMORY_HOME/log"

mkdir -p "$bin_dir" "$lib_dir" "$log_dir"

install -m 0755 "$src_dir/agent-shim" "$bin_dir/agent-shim"
install -m 0755 "$src_dir/watcher.py" "$lib_dir/watcher.py"

# Seed config.toml from the example, but never clobber an existing one — the
# user's tuning must survive re-installs.
config_file="$AGENT_MEMORY_HOME/config.toml"
if [[ ! -f "$config_file" ]]; then
  install -m 0644 "$src_dir/config.toml.example" "$config_file"
  echo "  wrote default config: $config_file"
else
  echo "  kept existing config: $config_file"
fi

for a in "${AGENTS[@]}"; do
  ln -sf "agent-shim" "$bin_dir/$a"
  echo "  shimmed: $a -> agent-shim"
done

echo
echo "Installed to $AGENT_MEMORY_HOME"
echo
echo "Add this to the TOP of your ~/.bashrc (or ~/.zshrc), then open a new shell:"
echo
echo "    export PATH=\"$bin_dir:\$PATH\""
echo
echo "Verify with:  type -a claude   # the first hit should be $bin_dir/claude"
echo "Uninstall:    remove that PATH line and  rm -rf $AGENT_MEMORY_HOME"
