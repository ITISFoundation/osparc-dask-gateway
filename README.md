# oSparc-Dask-Gateway

[![osparc-dask-gateway CI](https://github.com/ITISFoundation/osparc-dask-gateway/actions/workflows/gateway.yml/badge.svg)](https://github.com/ITISFoundation/osparc-dask-gateway/actions/workflows/gateway.yml)

[![codecov](https://codecov.io/gh/ITISFoundation/osparc-dask-gateway/branch/master/graph/badge.svg?token=I637tqTNuI)](https://codecov.io/gh/ITISFoundation/osparc-dask-gateway)

## osparc-gateway-server

Implements an osparc specific backend for the [dask gateway](https://gateway.dask.org/) based on [docker-swarm](https://docs.docker.com/engine/swarm/).

Each dask worker is implemented as a *docker service* which will be run as an oSparc Dask-based sidecar. Each machine in the swarm will run 1 worker.

## development

1. Start the dask gateway server with oSparc plugin

    ```console
    make build-devel
    make up-devel
    ```

2. In a second console create a python virtual env and install the gateway

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install dask_gateway
    ```

3. In python connect to the gateway

    ```python
    import dask_gateway
    g = dask_gateway.Gateway(address="http://127.0.0.1:8000", auth=dask_gateway.BasicAuth("whatever", "asdf"))
    g.list_clusters()
    c = g.new_cluster()
    ```
