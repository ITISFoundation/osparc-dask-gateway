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


exec python3 /opt/startup.py