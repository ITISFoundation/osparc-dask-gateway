# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name

from typing import Any, AsyncIterator, Dict

import aiodocker
import pytest
from osparc_gateway_server.backend.osparc import _is_task_running
from pytest_mock.plugin import MockerFixture


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
    print(f"--> created docker service {service=}")
    inspected_service = await async_docker_client.services.inspect(service["ID"])
    print(f"--> service inspected returned {inspected_service=}")
    yield inspected_service
    # cleanup
    await async_docker_client.services.delete(service["ID"])


async def test_is_task_running(
    docker_swarm,
    minimal_config,
    async_docker_client: aiodocker.Docker,
    docker_service: Dict[str, Any],
    mocker: MockerFixture,
):
    mocked_logger = mocker.MagicMock()
    await _is_task_running(
        async_docker_client, docker_service["Spec"]["Name"], mocked_logger
    )
