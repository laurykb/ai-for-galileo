#!/usr/bin/env bash
# Lance le front Next.js v3 sur son port dédié (3001 par défaut).
# Charge nvm si présent pour garantir Node >= 20.
set -e
cd "$(dirname "$0")"
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && nvm use default >/dev/null
[ -d node_modules ] || npm install
exec npm run dev -- --port "${PORT:-3001}"
