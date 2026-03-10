#!/usr/bin/env bash
set -euo pipefail

SSH_BASTION_HOST="${SSH_BASTION_HOST:-ssh.guard-e-loo.co.uk}"
SSH_BASTION_USER="${SSH_BASTION_USER:-gel-user-controller-1}"
SSH_KEY_PATH="${SSH_KEY_PATH:-/etc/gel-controller/ssh/id_ed25519}"
REMOTE_BIND_ADDRESS="${REMOTE_BIND_ADDRESS:-127.0.0.1}"
REMOTE_PORT="${REMOTE_PORT:-20001}"
LOCAL_TARGET_HOST="${LOCAL_TARGET_HOST:-127.0.0.1}"
LOCAL_TARGET_PORT="${LOCAL_TARGET_PORT:-8765}"

if [[ -z "${REMOTE_PORT}" ]]; then
  echo "REMOTE_PORT is required (for example: 20001)" >&2
  exit 1
fi

if command -v autossh >/dev/null 2>&1; then
  export AUTOSSH_GATETIME=0
  SSH_BIN="autossh"
else
  SSH_BIN="ssh"
fi

exec "${SSH_BIN}" \
  -N \
  -i "${SSH_KEY_PATH}" \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes \
  -R "${REMOTE_BIND_ADDRESS}:${REMOTE_PORT}:${LOCAL_TARGET_HOST}:${LOCAL_TARGET_PORT}" \
  "${SSH_BASTION_USER}@${SSH_BASTION_HOST}"
