#!/bin/bash

USER="$1"
IP="$2"

# On force :
# - pas d'interaction
# - timeout court
# - pas de blocage
ssh \
  -o BatchMode=yes \
  -o ConnectTimeout=3 \
  -o StrictHostKeyChecking=no \
  "$USER@$IP" \
  shutdown now \
  >/dev/null 2>&1 &

exit 0
