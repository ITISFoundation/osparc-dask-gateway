# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name

import asyncio
import socket
from typing import Any, Awaitable, Callable, Dict

import pytest
from _dask_helpers import DaskGatewayServer
from _host_helpers import get_this_computer_ip
from _pytest.monkeypatch import MonkeyPatch
from aiodocker import Docker
from dask_gateway import Gateway
from faker import Faker
from tenacity import retry
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_fixed

PASSWORD = "asdf"
TMP_FOLDER = "/tmp/gateway"


@pytest.fixture
def minimal_config(
    loop: asyncio.AbstractEventLoop,
    monkeypatch: MonkeyPatch,
    faker: Faker,
):
    monkeypatch.setenv("GATEWAY_VOLUME_NAME", faker.pystr())
    monkeypatch.setenv("GATEWAY_WORK_FOLDER", "/tmp/pytest_work_folder")
    monkeypatch.setenv("GATEWAY_WORKERS_NETWORK", faker.pystr())
    monkeypatch.setenv("GATEWAY_SERVER_NAME", get_this_computer_ip())
    monkeypatch.setenv("COMPUTATIONAL_SIDECAR_VOLUME_NAME", faker.pystr())
    monkeypatch.setenv(
        "COMPUTATIONAL_SIDECAR_IMAGE",
        "itisfoundation/dask-sidecar:master-github-latest",
    )


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
        print(f"found cluster: {clusters[0]=}")

        # Shutdown the cluster
        await cluster.shutdown()  # type: ignore

        clusters = await gateway_client.list_clusters()
        assert clusters == []


@pytest.fixture
async def gateway_worker_network(
    local_dask_gateway_server: DaskGatewayServer,
    docker_network: Callable[[str], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    network = await docker_network(
        local_dask_gateway_server.server.backend.settings.GATEWAY_WORKERS_NETWORK
    )
    return network


@pytest.mark.skip(reason="not ready yet")
async def test_cluster_scale(
    docker_swarm: None,
    docker_network,
    minimal_config: None,
    gateway_client: Gateway,
    gateway_worker_network: Dict[str, Any],
):

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
        await asyncio.sleep(5)
        # mocked_service_create_func.assert_called()
        # await wait_for_n_services(2)

        # and 2 corresponding containers
        # FIXME: we need a running container, waiting for PR2652
        # await wait_for_n_containers(2)

        async with cluster.get_client(set_as_default=False) as client:
            res = await client.submit(lambda x: x + 1, 1)  # type: ignore
            assert res == 2

        # Scale down
        await cluster.scale(1)

        # we should have 1 service
        await wait_for_n_services(1)

        # and 1 corresponding container
        # FIXME: we need a running container, waiting for PR2652
        # await wait_for_n_containers(1)

        # Can still compute
        async with cluster.get_client(set_as_default=False) as client:
            res = await client.submit(lambda x: x + 1, 1)  # type: ignore
            assert res == 2

        # Shutdown the cluster
        await cluster.shutdown()  # type: ignore

        # we should have no service
        await wait_for_n_services(0)

        # and no corresponding container
        await wait_for_n_containers(0)


@pytest.mark.skip("not ready")
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
            # FIXME: we need a running container, waiting for PR2652
            await wait_for_n_containers(3)

            async with cluster1.get_client(set_as_default=False) as client:
                res = await client.submit(lambda x: x + 1, 1)  # type: ignore
                assert res == 2

            async with cluster2.get_client(set_as_default=False) as client:
                res = await client.submit(lambda x: x + 1, 1)  # type: ignore
                assert res == 2

            # Shutdown the cluster1
            await cluster1.shutdown()  # type: ignore
            await cluster2.shutdown()  # type: ignore

            # we should have no service
            await wait_for_n_services(0)

            # and no corresponding container
            await wait_for_n_containers(0)
