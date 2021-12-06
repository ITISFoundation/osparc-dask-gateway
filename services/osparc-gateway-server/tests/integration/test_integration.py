import asyncio
from typing import List

import pytest
from aiodocker import Docker
from aiodocker.docker import DockerContainer
from aiodocker.exceptions import DockerError
from aiodocker.services import DockerServices
from dask import base
from dask_gateway import BasicAuth, Gateway
from dask_gateway_server.app import DaskGateway
from osparc_gateway_server.backend.osparc import (
    OsparcClusterConfig,
    UnsafeOsparcBackend,
)
from traitlets.config import Config

PASSWORD = "asdf"
TMP_FOLDER = "/tmp/gateway"


class TestGateway(object):
    def __init__(self):
        c = Config()
        c.DaskGateway.backend_class = UnsafeOsparcBackend
        c.DaskGateway.Backend.cluster_config_class = OsparcClusterConfig
        c.DaskGateway.address = "127.0.0.1:0"
        c.Proxy.address = "127.0.0.1:0"
        c.DaskGateway.authenticator_class = (
            "dask_gateway_server.auth.SimpleAuthenticator"
        )
        c.DaskGateway.Authenticator.password = PASSWORD
        c.OsparcBackend.clusters_directory = TMP_FOLDER

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
    containers = list()
    try:
        async with Docker() as docker_client:
            containers = await docker_client.containers.list()
    except DockerError:
        print("Docker Error")

    return containers


async def list_services() -> List[DockerServices]:
    services = list()
    try:
        async with Docker() as docker_client:
            services = await docker_client.services.list()
    except DockerError:
        print("Docker Error")

    return services


async def with_retries(f, N, wait=0.1, *args):
    for i in range(N):
        try:
            await f(*args)
            break
        except Exception:
            if i < N - 1:
                await asyncio.sleep(wait)
            else:
                raise


# this should have spawned two services
async def wait_for_n_services(n: int):
    services = await list_services()
    assert len(services) == n


async def wait_for_n_containers(n: int):
    containers = await list_containers()
    assert len(containers) == n


@pytest.mark.asyncio
async def test_cluster_start_stop():
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


@pytest.mark.asyncio
async def test_cluster_scale():
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
                await with_retries(wait_for_n_services, 20, 1.0, 2)

                # and 2 corresponding containers
                await with_retries(wait_for_n_containers, 20, 1.0, 2)

                async with cluster.get_client(set_as_default=False) as client:
                    res = await client.submit(lambda x: x + 1, 1)
                    assert res == 2

                # Scale down
                await cluster.scale(1)

                # we should have 1 service
                await with_retries(wait_for_n_services, 20, 1.0, 1)

                # and 1 corresponding container
                await with_retries(wait_for_n_containers, 20, 1.0, 1)

                # Can still compute
                async with cluster.get_client(set_as_default=False) as client:
                    res = await client.submit(lambda x: x + 1, 1)
                    assert res == 2

                workers = cluster.scheduler_info.get("workers")
                # Shutdown the cluster
                await cluster.shutdown()

                # we should have no service
                await with_retries(wait_for_n_services, 20, 1.0, 0)

                # and no corresponding container
                await with_retries(wait_for_n_containers, 20, 1.0, 0)


@pytest.mark.asyncio
async def test_multiple_clusters():
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
                    await with_retries(wait_for_n_services, 20, 1.0, 3)

                    # and 3 corresponding containers
                    await with_retries(wait_for_n_containers, 20, 1.0, 3)

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
                    await with_retries(wait_for_n_services, 20, 1.0, 0)

                    # and no corresponding container
                    await with_retries(wait_for_n_containers, 20, 1.0, 0)
