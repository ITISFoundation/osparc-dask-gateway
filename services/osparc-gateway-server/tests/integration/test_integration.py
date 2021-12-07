import asyncio
import json
from typing import AsyncIterator, List, NamedTuple

import pytest
import traitlets.config
from aiodocker import Docker
from aiodocker.docker import DockerContainer
from aiodocker.exceptions import DockerError
from aiodocker.services import DockerServices
from dask_gateway import BasicAuth, Gateway
from dask_gateway_server.app import DaskGateway
from osparc_gateway_server.backend.osparc import (
    OsparcClusterConfig,
    UnsafeOsparcBackend,
)
from tenacity import retry
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_fixed

PASSWORD = "asdf"
TMP_FOLDER = "/tmp/gateway"


@pytest.fixture
def minimal_config(monkeypatch):
    monkeypatch.setenv("GATEWAY_VOLUME_NAME", "atestvolumename")
    monkeypatch.setenv("GATEWAY_WORK_FOLDER", "atestworkfolder")
    monkeypatch.setenv("GATEWAY_WORKERS_NETWORK", "atestnetwork")
    monkeypatch.setenv("GATEWAY_SERVER_NAME", "atestserver")
    monkeypatch.setenv("COMPUTATIONAL_SIDECAR_IMAGE", "test/localpytest:latest")


class DaskGatewayServer(NamedTuple):
    address: str
    proxy_address: str
    password: str
    server: DaskGateway


@pytest.fixture
async def local_dask_gateway_server(loop) -> AsyncIterator[DaskGatewayServer]:
    """this code is more or less copy/pasted from dask-gateway repo"""
    c = traitlets.config.Config()
    c.DaskGateway.backend_class = UnsafeOsparcBackend  # type: ignore
    c.DaskGateway.Backend.cluster_config_class = OsparcClusterConfig  # type: ignore
    c.DaskGateway.address = "127.0.0.1:0"  # type: ignore
    c.DaskGateway.log_level = "DEBUG"  # type: ignore
    c.Proxy.address = "127.0.0.1:0"  # type: ignore
    c.DaskGateway.authenticator_class = "dask_gateway_server.auth.SimpleAuthenticator"  # type: ignore
    c.SimpleAuthenticator.password = PASSWORD  # type: ignore
    c.OsparcBackend.clusters_directory = TMP_FOLDER  # type: ignore
    print(f"--> local dask gateway config: {c}")
    dask_gateway_server = DaskGateway(config=c)
    dask_gateway_server.initialize([])  # that is a shitty one!
    print("--> local dask gateway server initialized")
    await dask_gateway_server.setup()
    await dask_gateway_server.backend.proxy._proxy_contacted  # pylint: disable=protected-access
    print("--> local dask gateway server setup completed")
    yield DaskGatewayServer(
        f"http://{dask_gateway_server.backend.proxy.address}",
        f"gateway://{dask_gateway_server.backend.proxy.tcp_address}",
        c.SimpleAuthenticator.password,  # type: ignore
        dask_gateway_server,
    )
    print("--> local dask gateway server switching off...")
    await dask_gateway_server.cleanup()
    print("...done")


@pytest.fixture
async def gateway_client(
    local_dask_gateway_server: DaskGatewayServer,
) -> AsyncIterator[Gateway]:
    async with Gateway(
        local_dask_gateway_server.address,
        local_dask_gateway_server.proxy_address,
        asynchronous=True,
        auth=BasicAuth(username="pytest_user", password=PASSWORD),
    ) as gateway:
        assert gateway
        print(f"--> {gateway=} created")
        cluster_options = await gateway.cluster_options()
        gateway_versions = await gateway.get_versions()
        clusters_list = await gateway.list_clusters()
        print(f"--> {gateway_versions=}, {cluster_options=}, {clusters_list=}")
        for option in cluster_options.items():
            print(f"--> {option=}")
        yield gateway


@retry(reraise=True, stop=stop_after_attempt(20), wait=wait_fixed(0.1))
async def wait_for_n_services(n: int):
    async with Docker() as docker_client:
        assert len(await docker_client.services.list()) == n


@retry(reraise=True, stop=stop_after_attempt(20), wait=wait_fixed(0.1))
async def wait_for_n_containers(n: int):
    async with Docker() as docker_client:
        assert len(await docker_client.containers.list()) == n


async def test_cluster_start_stop(minimal_config, gateway_client: Gateway):
    # No currently running clusters
    clusters = await gateway_client.list_clusters()
    assert clusters == []

    # create one cluster
    async with gateway_client.new_cluster() as cluster:
        clusters = await gateway_client.list_clusters()
        assert len(clusters)
        assert clusters[0].name == cluster.name

        # Shutdown the cluster
        await cluster.shutdown()

        clusters = await gateway_client.list_clusters()
        assert clusters == []


async def test_cluster_scale(minimal_config, gateway_client: Gateway):
    # No currently running clusters
    clusters = await gateway_client.list_clusters()
    assert clusters == []
    # create one cluster
    async with gateway_client.new_cluster() as cluster:

        # Cluster is now present in list
        clusters = await gateway_client.list_clusters()
        assert len(clusters)
        assert clusters[0].name == cluster.name

        # Scale up, connect, and compute
        await cluster.scale(2)

        # we should have 2 services
        await wait_for_n_services(2)

        # and 2 corresponding containers
        await wait_for_n_containers(2)

        async with cluster.get_client(set_as_default=False) as client:
            res = await client.submit(lambda x: x + 1, 1)
            assert res == 2

        # Scale down
        await cluster.scale(1)

        # we should have 1 service
        await wait_for_n_services(1)

        # and 1 corresponding container
        await wait_for_n_containers(1)

        # Can still compute
        async with cluster.get_client(set_as_default=False) as client:
            res = await client.submit(lambda x: x + 1, 1)
            assert res == 2

        # Shutdown the cluster
        await cluster.shutdown()

        # we should have no service
        await wait_for_n_services(0)

        # and no corresponding container
        await wait_for_n_containers(0)


async def test_multiple_clusters(minimal_config, gateway_client: Gateway):
    # No currently running clusters
    clusters = await gateway_client.list_clusters()
    assert clusters == []
    # create one cluster
    async with gateway_client.new_cluster() as cluster1:
        async with gateway_client.new_cluster() as cluster2:
            # Cluster is now present in list
            clusters = await gateway_client.list_clusters()
            assert len(clusters) == 2
            assert cluster1.name in [c.name for c in clusters]
            assert cluster2.name in [c.name for c in clusters]

            # Scale up, connect, and compute
            await cluster1.scale(1)
            await cluster2.scale(2)

            # we should have 3 services
            await wait_for_n_services(3)

            # and 3 corresponding containers
            await wait_for_n_containers(3)

            async with cluster1.get_client(set_as_default=False) as client:
                res = await client.submit(lambda x: x + 1, 1)
                assert res == 2

            async with cluster2.get_client(set_as_default=False) as client:
                res = await client.submit(lambda x: x + 1, 1)
                assert res == 2

            # Shutdown the cluster1
            await cluster1.shutdown()
            await cluster2.shutdown()

            # we should have no service
            await wait_for_n_services(0)

            # and no corresponding container
            await wait_for_n_containers(0)
