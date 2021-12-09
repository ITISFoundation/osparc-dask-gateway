# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name

import asyncio
import socket
from os import name
from typing import AsyncIterator, NamedTuple

import aiodocker
import pytest
import traitlets.config
from aiodocker import Docker
from dask_gateway import BasicAuth, Gateway
from dask_gateway_server.app import DaskGateway
from osparc_gateway_server.backend.osparc import (
    OsparcClusterConfig,
    UnsafeOsparcBackend,
)
from pytest_mock.plugin import MockerFixture
from tenacity import retry
from tenacity._asyncio import AsyncRetrying
from tenacity.stop import stop_after_attempt
from tenacity.wait import wait_fixed

PASSWORD = "asdf"
TMP_FOLDER = "/tmp/gateway"


@pytest.fixture
async def gateway_workers_network(
    async_docker_client: aiodocker.Docker,
) -> AsyncIterator[str]:
    _NETWORK_NAME = "pytest_swarm_network"
    network = await async_docker_client.networks.create(
        config={"Name": _NETWORK_NAME, "Driver": "overlay"}
    )
    assert network

    yield _NETWORK_NAME
    await network.delete()
    async for attempt in AsyncRetrying(reraise=True, wait=wait_fixed(1)):
        with attempt:
            print(f"<-- waiting for network '{_NETWORK_NAME}' deletion...")
            list_of_network_names = [
                network["Name"] for network in await async_docker_client.networks.list()
            ]
            assert _NETWORK_NAME not in list_of_network_names
        print(f"<-- network '{_NETWORK_NAME}' deleted")


@pytest.fixture
async def gateway_volume_name(
    async_docker_client: aiodocker.Docker,
) -> AsyncIterator[str]:
    _VOLUME_NAME = "pytest_gateway_volume"
    volume = await async_docker_client.volumes.create(config={"Name": _VOLUME_NAME})
    assert volume
    yield _VOLUME_NAME
    async for attempt in AsyncRetrying(reraise=True, wait=wait_fixed(1)):
        with attempt:
            print(f"<-- deleting volume '{_VOLUME_NAME}'...")
            await volume.delete()
        print(f"<-- volume '{_VOLUME_NAME}' deleted")


def get_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(("10.255.255.255", 1))
        IP = s.getsockname()[0]
    except Exception:  # pylint: disable=broad-except
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP


@pytest.fixture
def minimal_config(
    docker_swarm, monkeypatch, gateway_workers_network: str, gateway_volume_name: str
):
    monkeypatch.setenv("GATEWAY_VOLUME_NAME", gateway_volume_name)
    monkeypatch.setenv("GATEWAY_WORK_FOLDER", "/tmp/pytest_work_folder")
    monkeypatch.setenv("GATEWAY_WORKERS_NETWORK", gateway_workers_network)
    monkeypatch.setenv("GATEWAY_SERVER_NAME", get_ip())
    monkeypatch.setenv(
        "COMPUTATIONAL_SIDECAR_IMAGE",
        "itisfoundation/dask-sidecar:master-github-latest",
    )
    monkeypatch.setenv(
        "COMPUTATIONAL_SIDECAR_IMAGE",
        "local/dask-sidecar:production",
    )


class DaskGatewayServer(NamedTuple):
    address: str
    proxy_address: str
    password: str
    server: DaskGateway


@pytest.fixture
async def local_dask_gateway_server(
    loop: asyncio.AbstractEventLoop,
    minimal_config: None,
) -> AsyncIterator[DaskGatewayServer]:
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
        print(f"--> {gateway} created")
        cluster_options = await gateway.cluster_options()
        gateway_versions = await gateway.get_versions()
        clusters_list = await gateway.list_clusters()
        print(f"--> {gateway_versions}, {cluster_options}, {clusters_list}")
        for option in cluster_options.items():
            print(f"--> {option}")
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
        await cluster.shutdown()  # type: ignore

        clusters = await gateway_client.list_clusters()
        assert clusters == []

@pytest.mark.skip("not ready")
async def test_cluster_scale(docker_swarm, minimal_config, gateway_client: Gateway, mocker: MockerFixture):
    mocked_service_create_func = mocker.patch("aiodocker.services.DockerServices.create", return_value={"ID": "pytest_mock_id"})
    mocked_service_inspec_func = mocker.patch("aiodocker.services.DockerServices.inspect", return_value={"ID": "pytest_mock_id", "Spec": {"Name": "pytest_fake_service"}})
    mocked_is_task_running_func = mocker.patch("osparc_gateway_server.backend.osparc._is_task_running", return_value=True)
    mocked_service_delete_func = mocker.patch("aiodocker.services.DockerServices.delete", return_value=None)

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
        mocked_service_create_func.assert_called()
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
