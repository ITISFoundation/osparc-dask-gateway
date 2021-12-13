# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name

from typing import Any, AsyncIterator, Dict, NewType
from unittest import mock

import aiodocker
import pytest
from osparc_gateway_server.backend.osparc import (
    _get_docker_network_id,
    _is_task_running,
)
from pytest_mock.plugin import MockerFixture
from tenacity._asyncio import AsyncRetrying
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_fixed


@pytest.fixture
def minimal_config(monkeypatch):
    monkeypatch.setenv("GATEWAY_VOLUME_NAME", "atestvolumename")
    monkeypatch.setenv("GATEWAY_WORK_FOLDER", "atestworkfolder")
    monkeypatch.setenv("GATEWAY_WORKERS_NETWORK", "atestnetwork")
    monkeypatch.setenv("GATEWAY_SERVER_NAME", "atestserver")
    monkeypatch.setenv("COMPUTATIONAL_SIDECAR_IMAGE", "test/localpytest:latest")


@pytest.fixture
async def docker_service(
    async_docker_client: aiodocker.Docker,
) -> AsyncIterator[Dict[str, Any]]:
    TaskTemplate = {
        "ContainerSpec": {
            "Image": "redis",
        },
    }
    service = await async_docker_client.services.create(
        task_template=TaskTemplate, name="my_service"
    )
    assert service
    print(f"--> created docker service {service}")
    inspected_service = await async_docker_client.services.inspect(service["ID"])
    print(f"--> service inspected returned {inspected_service}")

    yield inspected_service
    # cleanup
    await async_docker_client.services.delete(service["ID"])


@pytest.fixture
async def running_service(
    async_docker_client: aiodocker.Docker, docker_service: Dict[str, Any]
) -> Dict[str, Any]:
    async for attempt in AsyncRetrying(
        reraise=True, wait=wait_fixed(1), stop=stop_after_delay(60)
    ):
        with attempt:
            tasks = await async_docker_client.tasks.list(
                filters={"service": f"{docker_service['Spec']['Name']}"}
            )
            task_states = [task["Status"]["State"] for task in tasks]
            num_running = sum(current == "running" for current in task_states)
            print(f"--> service task states {task_states=}")
            assert num_running == 1
            print(f"--> service {docker_service['Spec']['Name']} is running now")
            return docker_service
    raise AssertionError(f"service {docker_service=} could not start")


@pytest.fixture
def mocked_logger(mocker: MockerFixture) -> mock.MagicMock:
    return mocker.MagicMock()


async def test_is_task_running(
    docker_swarm,
    minimal_config,
    async_docker_client: aiodocker.Docker,
    running_service: Dict[str, Any],
    mocked_logger: mock.MagicMock,
):

    # this service exists and run
    assert (
        await _is_task_running(
            async_docker_client, running_service["Spec"]["Name"], mocked_logger
        )
        == True
    )

    # check unknown service raises error
    with pytest.raises(aiodocker.DockerError):
        await _is_task_running(async_docker_client, "unknown_service", mocked_logger)


@pytest.fixture
async def docker_swarm_network(
    async_docker_client: aiodocker.Docker, network_driver: str
) -> AsyncIterator[Dict[str, Any]]:
    network: aiodocker.docker.DockerNetwork = await async_docker_client.networks.create(
        config={"Name": "pytest_docker_swarm_network", "Driver": network_driver}
    )
    yield await network.show()
    # cleanup
    await network.delete()


@pytest.mark.parametrize(
    "network_driver, expected_found", [("bridge", False), ("overlay", True)]
)
async def test_get_network_id(
    docker_swarm,
    async_docker_client: aiodocker.Docker,
    docker_swarm_network: Dict[str, Any],
    mocked_logger: mock.MagicMock,
    expected_found: bool,
):
    # wrong name shall raise
    with pytest.raises(ValueError):
        await _get_docker_network_id(
            async_docker_client, "a_fake_network_name", mocked_logger
        )
    if expected_found:
        network_id = await _get_docker_network_id(
            async_docker_client, docker_swarm_network["Name"], mocked_logger
        )
        assert network_id == docker_swarm_network["Id"]
    else:
        with pytest.raises(ValueError):
            await _get_docker_network_id(
                async_docker_client, "a_fake_network_name", mocked_logger
            )
