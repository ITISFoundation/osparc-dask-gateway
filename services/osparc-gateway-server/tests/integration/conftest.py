import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Union

import aiodocker
import dask_gateway
import pytest
import traitlets
import traitlets.config
from _dask_helpers import DaskGatewayServer
from _host_helpers import get_this_computer_ip
from _pytest.tmpdir import tmp_path
from dask_gateway_server.app import DaskGateway
from faker import Faker
from osparc_gateway_server.backend.osparc import OsparcBackend
from tenacity._asyncio import AsyncRetrying
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_fixed


@pytest.fixture
async def docker_network(
    async_docker_client: aiodocker.Docker,
) -> AsyncIterator[Callable[[str], Awaitable[Dict[str, Any]]]]:
    networks = []

    async def _network_creator(name: str) -> Dict[str, Any]:
        network = await async_docker_client.networks.create(
            config={"Name": name, "Driver": "overlay"}
        )
        assert network
        print(f"--> created network {network=}")
        networks.append(network)
        return await network.show()

    yield _network_creator

    # wait until all networks are really gone
    async def _wait_for_network_deletion(network: aiodocker.docker.DockerNetwork):
        network_name = (await network.show())["Name"]
        await network.delete()
        async for attempt in AsyncRetrying(
            reraise=True, wait=wait_fixed(1), stop=stop_after_delay(60)
        ):
            with attempt:
                print(f"<-- waiting for network '{network_name}' deletion...")
                list_of_network_names = [
                    n["Name"] for n in await async_docker_client.networks.list()
                ]
                assert network_name not in list_of_network_names
            print(f"<-- network '{network_name}' deleted")

    print(f"<-- removing all networks {networks=}")
    await asyncio.gather(*[_wait_for_network_deletion(network) for network in networks])


@pytest.fixture
async def docker_volume(
    async_docker_client: aiodocker.Docker,
) -> AsyncIterator[Callable[[str], Awaitable[Dict[str, Any]]]]:
    volumes = []

    async def _volume_creator(name: str) -> Dict[str, Any]:
        volume = await async_docker_client.volumes.create(config={"Name": name})
        assert volume
        print(f"--> created {volume=}")
        volumes.append(volume)
        return await volume.show()

    yield _volume_creator
    # cleanup
    async def _wait_for_volume_deletion(volume: aiodocker.docker.DockerVolume):
        inspected_volume = await volume.show()
        async for attempt in AsyncRetrying(reraise=True, wait=wait_fixed(1)):
            with attempt:
                print(f"<-- deleting volume '{inspected_volume['Name']}'...")
                await volume.delete()
            print(f"<-- volume '{inspected_volume['Name']}' deleted")

    await asyncio.gather(*[_wait_for_volume_deletion(v) for v in volumes])


@pytest.fixture
def gateway_password(faker: Faker) -> str:
    return faker.password()


@pytest.fixture()
def cluster_directory(tmp_path, faker: Faker) -> Path:
    dir = Path(tmp_path / faker.pystr())
    dir.mkdir(parents=True, exist_ok=True)
    assert dir.exists()
    return dir


def _convert_to_dict(c: Union[traitlets.config.Config, Dict]) -> Dict[str, Any]:
    converted_dict = {}
    for x, y in c.items():
        if isinstance(y, (dict, traitlets.config.Config)):
            converted_dict[x] = _convert_to_dict(y)
        else:
            converted_dict[x] = f"{y}"
    return converted_dict


@pytest.fixture
async def local_dask_gateway_server(
    minimal_config: None, gateway_password: str, cluster_directory: Path
) -> AsyncIterator[DaskGatewayServer]:
    """this code is more or less copy/pasted from dask-gateway repo"""
    c = traitlets.config.Config()
    c.DaskGateway.backend_class = OsparcBackend  # type: ignore
    c.DaskGateway.address = "127.0.0.1:0"  # type: ignore
    c.DaskGateway.log_level = "DEBUG"  # type: ignore
    c.Proxy.address = f"{get_this_computer_ip()}:0"  # type: ignore
    c.DaskGateway.authenticator_class = "dask_gateway_server.auth.SimpleAuthenticator"  # type: ignore
    c.SimpleAuthenticator.password = gateway_password  # type: ignore
    c.OsparcBackend.clusters_directory = f"{cluster_directory}"  # type: ignore
    # c.OsparcBackend.api_url = get_this_computer_ip()
    print(f"--> local dask gateway config: {json.dumps(_convert_to_dict(c), indent=2)}")
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
) -> AsyncIterator[dask_gateway.Gateway]:
    async with dask_gateway.Gateway(
        local_dask_gateway_server.address,
        local_dask_gateway_server.proxy_address,
        asynchronous=True,
        auth=dask_gateway.BasicAuth(
            username="pytest_user", password=local_dask_gateway_server.password
        ),
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
