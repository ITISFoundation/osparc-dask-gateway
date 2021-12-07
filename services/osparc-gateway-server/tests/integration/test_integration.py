import asyncio
from typing import List

import pytest
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
from traitlets.config import Config

PASSWORD = "asdf"
TMP_FOLDER = "/tmp/gateway"


@pytest.fixture
def minimal_config(monkeypatch):
    monkeypatch.setenv("GATEWAY_VOLUME_NAME", "atestvolumename")
    monkeypatch.setenv("GATEWAY_WORK_FOLDER", "atestworkfolder")
    monkeypatch.setenv("GATEWAY_WORKERS_NETWORK", "atestnetwork")
    monkeypatch.setenv("GATEWAY_SERVER_NAME", "atestserver")
    monkeypatch.setenv("COMPUTATIONAL_SIDECAR_IMAGE", "test/localpytest:latest")


class TestGateway:
    def __init__(self):
        c = Config()
        c.DaskGateway.backend_class = UnsafeOsparcBackend  # type: ignore
        c.DaskGateway.Backend.cluster_config_class = OsparcClusterConfig  # type: ignore
        c.DaskGateway.address = "127.0.0.1:0"  # type: ignore
        c.Proxy.address = "127.0.0.1:0"  # type: ignore
        c.DaskGateway.authenticator_class = (  # type: ignore
            "dask_gateway_server.auth.SimpleAuthenticator"
        )
        c.DaskGateway.Authenticator.password = PASSWORD  # type: ignore
        c.OsparcBackend.clusters_directory = TMP_FOLDER  # type: ignore

        self.config = c

    async def __aenter__(self):
        self.gateway = DaskGateway(config=self.config)
        self.gateway.initialize([])
        await self.gateway.setup()
        await self.gateway.backend.proxy._proxy_contacted
        self.address = f"http://{self.gateway.backend.proxy.address}"
        self.proxy_address = f"gateway://{self.gateway.backend.proxy.tcp_address}"
        return self

    async def __aexit__(self, *args):
        await self.gateway.cleanup()

    def gateway_client(self, **kwargs):
        defaults = {
            "address": self.address,
            "proxy_address": self.proxy_address,
            "asynchronous": True,
        }
        defaults.update(kwargs)
        return Gateway(**defaults)


async def list_containers() -> List[DockerContainer]:
    async with Docker() as docker_client:
        return await docker_client.containers.list()


async def list_services() -> List[DockerServices]:
    async with Docker() as docker_client:
        return await docker_client.services.list()


@retry(reraise=True, stop=stop_after_attempt(20), wait=wait_fixed(0.1))
async def wait_for_n_services(n: int):
    services = await list_services()
    assert len(services) == n


@retry(reraise=True, stop=stop_after_attempt(20), wait=wait_fixed(0.1))
async def wait_for_n_containers(n: int):
    containers = await list_containers()
    assert len(containers) == n


async def test_cluster_start_stop(minimal_config):
    async with TestGateway() as g:
        async with g.gateway_client(
            auth=BasicAuth(username=None, password=PASSWORD)
        ) as gateway:
            # No currently running clusters
            clusters = await gateway.list_clusters()
            assert clusters == []

            # create one cluster
            async with gateway.new_cluster() as cluster:
                clusters = await gateway.list_clusters()
                assert len(clusters)
                assert clusters[0].name == cluster.name

                # Shutdown the cluster
                await cluster.shutdown()

                clusters = await gateway.list_clusters()
                assert clusters == []


async def test_cluster_scale(minimal_config):
    async with TestGateway() as g:
        async with g.gateway_client(
            auth=BasicAuth(username=None, password=PASSWORD)
        ) as gateway:
            # No currently running clusters
            clusters = await gateway.list_clusters()
            assert clusters == []
            # create one cluster
            async with gateway.new_cluster() as cluster:

                # Cluster is now present in list
                clusters = await gateway.list_clusters()
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

                workers = cluster.scheduler_info.get("workers")
                # Shutdown the cluster
                await cluster.shutdown()

                # we should have no service
                await wait_for_n_services(0)

                # and no corresponding container
                await wait_for_n_containers(0)


async def test_multiple_clusters(minimal_config):
    async with TestGateway() as g:
        async with g.gateway_client(
            auth=BasicAuth(username=None, password=PASSWORD)
        ) as gateway:
            # No currently running clusters
            clusters = await gateway.list_clusters()
            assert clusters == []
            # create one cluster
            async with gateway.new_cluster() as cluster1:
                async with gateway.new_cluster() as cluster2:
                    # Cluster is now present in list
                    clusters = await gateway.list_clusters()
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
