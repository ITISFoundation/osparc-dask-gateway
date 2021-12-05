#!/bin/sh
set -o errexit
set -o nounset

IFS=$(printf '\n\t')

INFO="INFO: [$(basename "$0")] "

# BOOTING application ---------------------------------------------
echo "$INFO" "Booting as ..."
echo "  User    :$(id "$(whoami)")"
echo "  Workdir :$(pwd)"
echo "  env     :$(env)"


exec python3 /opt/volume_sync.py --sync-folder="${SYNC_FOLDER}" \
    --wait-period="${WAIT_PERIOD}" \
    --sync-interval="${SYNC_INTERVAL}" \
    --sync-timeout="${SYNC_TIMEOUT}" \
    --sync-type="${SYNC_TYPE}"
