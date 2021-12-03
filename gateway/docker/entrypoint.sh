#!/bin/sh
set -o errexit
set -o nounset

IFS=$(printf '\n\t')

INFO="INFO: [$(basename "$0")] "
WARNING="WARNING: [$(basename "$0")] "

# This entrypoint script:
#
# - Executes *inside* of the container upon start as --user [default root]
# - Notice that the container *starts* as --user [default root] but
#   *runs* as non-root user [scu]
#
echo "$INFO" "Entrypoint for gateway server ..."
echo "  User    :$(id "$(whoami)")"
echo "  Workdir :$(pwd)"
echo "  scuUser :$(id scu)"

USERNAME=scu
GROUPNAME=scu

DOCKER_MOUNT=/var/run/docker.sock
if stat $DOCKER_MOUNT >/dev/null 2>&1; then
    echo "$INFO detected docker socket is mounted, adding user to group..."
    GROUPID=$(stat --format=%g $DOCKER_MOUNT)
    GROUPNAME=scdocker

    if ! addgroup --gid "$GROUPID" $GROUPNAME >/dev/null 2>&1; then
        echo "$WARNING docker group with $GROUPID already exists, getting group name..."
        # if group already exists in container, then reuse name
        GROUPNAME=$(getent group "${GROUPID}" | cut --delimiter=: --fields=1)
        echo "$WARNING docker group with $GROUPID has name $GROUPNAME"
    fi
    adduser "$SC_USER_NAME" "$GROUPNAME"
fi

echo "$INFO ensuring write rights on folders ..."
chown --recursive $USERNAME:"$GROUPNAME" "${GATEWAY_WORK_FOLDER}"

echo "$INFO Starting gateway ..."
echo "  $SC_USER_NAME rights    : $(id "$SC_USER_NAME")"
echo "  local dir : $(ls -al)"
echo "  GATEWAY_WORK_FOLDER dir : $(ls -al "${GATEWAY_WORK_FOLDER}")"

exec gosu "$SC_USER_NAME" "$@"
