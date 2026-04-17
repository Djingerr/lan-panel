#!/bin/bash

USER="$1"
IP="$2"
OS_TYPE="$3"

if [ -z "$OS_TYPE" ]; then
  OS_TYPE="linux"
fi

if [ "$OS_TYPE" = "windows" ]; then
  REMOTE_CMD='shutdown /s /t 0'
else
  REMOTE_CMD='sudo /sbin/shutdown now'
fi

ssh \
  -o BatchMode=yes \
  -o ConnectTimeout=5 \
  -o StrictHostKeyChecking=no \
  "$USER@$IP" \
  "$REMOTE_CMD" \
  >/dev/null 2>&1 &

exit 0