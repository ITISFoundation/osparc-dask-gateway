from typing import Any, AsyncIterator, Dict

import aiodocker
import pytest


@pytest.fixture
def minimal_config(monkeypatch):
    monkeypatch.setenv("GATEWAY_VOLUME_NAME", "atestvolumename")
    monkeypatch.setenv("GATEWAY_WORK_FOLDER", "atestworkfolder")
    monkeypatch.setenv("GATEWAY_WORKERS_NETWORK", "atestnetwork")
    monkeypatch.setenv("GATEWAY_SERVER_NAME", "atestserver")
    monkeypatch.setenv("COMPUTATIONAL_SIDECAR_IMAGE", "test/localpytest:latest")


import asyncio


@pytest.fixture
async def docker_client(
    loop: asyncio.AbstractEventLoop,
) -> AsyncIterator[aiodocker.Docker]:
    async with aiodocker.Docker() as docker_client:
        yield docker_client


@pytest.fixture
async def docker_service(
    docker_client: aiodocker.Docker,
) -> AsyncIterator[Dict[str, Any]]:
    TaskTemplate = {
        "ContainerSpec": {
            "Image": "redis",
        },
    }
    service = await docker_client.services.create(
        task_template=TaskTemplate, name="my_service"
    )
    assert service
    print(f"--> created docker service {service=}")
    inspected_service = await docker_client.services.inspect(service["ID"])
    print(f"--> service inspected returned {inspected_service=}")
    yield inspected_service
    # cleanup
    await docker_client.services.delete(service["ID"])


from osparc-gateway-server.backend.osparc import _is_task_running
from pytest_mock.plugin import MockerFixture


async def test_is_task_running(
    minimal_config,
    docker_client: aiodocker.Docker,
    docker_service: aiodocker.Docker,
    mocker: MockerFixture,
):
    mocked_logger = mocker.MagicMock()
    await _is_task_running(docker_client, docker_service["Spec"]["Name"], mocked_logger)
