#!/bin/bash
# Resolve node for hook environments where PATH lacks nvm/fnm/volta
# Usage: bash resolve-node.sh <script.cjs> [args...]
#   or:  source resolve-node.sh  (only sets up PATH, no exec)

_resolve_node_path() {
  # Already available?
  command -v node &>/dev/null && return 0

  # nvm
  export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
  [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && return 0

  # fnm
  [ -d "$HOME/.fnm" ] && eval "$("$HOME/.fnm/fnm" env 2>/dev/null)" && command -v node &>/dev/null && return 0
  [ -d "$HOME/Library/Application Support/fnm" ] && eval "$("$HOME/Library/Application Support/fnm/fnm" env 2>/dev/null)" && command -v node &>/dev/null && return 0

  # volta
  [ -d "$HOME/.volta" ] && export PATH="$HOME/.volta/bin:$PATH" && command -v node &>/dev/null && return 0

  # Homebrew (macOS)
  for brew_prefix in /opt/homebrew /usr/local; do
    [ -x "$brew_prefix/bin/node" ] && export PATH="$brew_prefix/bin:$PATH" && return 0
  done

  return 1
}

_resolve_node_path

# If called with arguments, exec node with those args
if [ $# -gt 0 ]; then
  exec node "$@"
fi
