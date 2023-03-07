# oSparc-Dask-Gateway

[![osparc-dask-gateway CI](https://github.com/ITISFoundation/osparc-dask-gateway/actions/workflows/gateway.yml/badge.svg)](https://github.com/ITISFoundation/osparc-dask-gateway/actions/workflows/gateway.yml) [![codecov](https://codecov.io/gh/ITISFoundation/osparc-dask-gateway/branch/main/graph/badge.svg?token=I637tqTNuI)](https://codecov.io/gh/ITISFoundation/osparc-dask-gateway) [![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

Implements an [oSparc](https://github.com/ITISFoundation/osparc-simcore)-compatible backend for the [dask gateway](https://gateway.dask.org/) using the [docker-swarm](https://docs.docker.com/engine/swarm/) orchestration.


## Concept

A computers cluster is managed by some user. All the computers are part of a docker swarm. The osparc-gateway-server runs in a container on one of the *manager* nodes.

The oSparc connects to that gateway and starts a Dask cluster made of a *itisfoundation/dask-sidecar* as a *docker service*. In turn the same *itisfoundation/dask-sidecar* will be started as *dask-worker* to do some computational work. Each computer will host one dask-worker.

## Usage

1. Get any number of computers (on-premise, AWS, PIs)
2. On the manager mode run:

   ```bash
   docker swarm init
   ```

3. On each of the other computers run the command that 2. outputed (which should look like):

   ```bash
   docker swarm join --token TOKEN IP_MANAGER:2377
   ```

4. On the manager node:

   ```bash
   # this will list all the computer nodes that joined the cluster
   docker node ls
   ```

5. Start the gateway by executing:

   ```bash
   git clone https://github.com/ITISFoundation/osparc-dask-gateway.git
   cd osparc-dask-gateway
   make up
   # this should output the address and password on how to connect with the gateway
   ```

## Testing the gateway

1. In a second console create a python virtual environment

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install dask_gateway
    python
    ```

2. In the python environment try

    ```python
    import dask_gateway
    g = dask_gateway.Gateway(address="http://GATEWAY_IP:8000", auth=dask_gateway.BasicAuth("user", "GATEWAY_PASSWORD"))
    g.list_clusters()
    c = g.new_cluster() # the first time might be a bit long, as the scheduler docker image is pulled from docker registry
    c.scale(1) # this will create a dask-worker in the cluster, this can also take some time the first times it is pulled on each of the computer nodes
    client = c.get_client()

    ```
