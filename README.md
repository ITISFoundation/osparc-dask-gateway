# osparc-dask-gatway

[![osparc-dask-gateway CI](https://github.com/ITISFoundation/osparc-dask-gateway/actions/workflows/gateway.yml/badge.svg)](https://github.com/ITISFoundation/osparc-dask-gateway/actions/workflows/gateway.yml)

[![codecov](https://codecov.io/gh/ITISFoundation/osparc-dask-gateway/branch/master/graph/badge.svg?token=I637tqTNuI)](https://codecov.io/gh/ITISFoundation/osparc-dask-gateway)

## osparc-gateway-server

Implements an osparc specific backend for the [dask gateway](https://gateway.dask.org/) based on [docker-swarm](https://docs.docker.com/engine/swarm/).

Each dask worker is implemented as a *docker service* which will be run as an oSparc Dask-based sidecar. Each machine in the swarm will run 1 worker.

## volume-sync

Tool to keep small rarely modified docker volumes in sync among swarm nodes. Taken and modified from [here](https://github.com/granlem/docker-volume-sync/)
