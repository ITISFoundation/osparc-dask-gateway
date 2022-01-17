# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict
from unittest import mock

import aiodocker
import pytest
from dask_gateway_server.backends.db_base import Cluster, JobStatus
from faker import Faker
from osparc_gateway_server.backend.settings import AppSettings
from osparc_gateway_server.backend.utils import (
    DockerSecret,
    create_or_update_secret,
    create_service_config,
    delete_secrets,
    get_network_id,
    is_service_task_running,
)
from pytest_mock.plugin import MockerFixture
from tenacity._asyncio import AsyncRetrying
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_fixed


@pytest.fixture
def minimal_config(monkeypatch):
    monkeypatch.setenv("GATEWAY_WORKERS_NETWORK", "atestnetwork")
    monkeypatch.setenv("GATEWAY_SERVER_NAME", "atestserver")
    monkeypatch.setenv("COMPUTATIONAL_SIDECAR_IMAGE", "test/localpytest:latest")
    monkeypatch.setenv(
        "COMPUTATIONAL_SIDECAR_VOLUME_NAME", "sidecar_computational_volume_name"
    )


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
        await is_service_task_running(
            async_docker_client, running_service["Spec"]["Name"], mocked_logger
        )
        == True
    )

    # check unknown service raises error
    with pytest.raises(aiodocker.DockerError):
        await is_service_task_running(
            async_docker_client, "unknown_service", mocked_logger
        )


@pytest.fixture
async def docker_network(
    async_docker_client: aiodocker.Docker, network_driver: str, faker: Faker
) -> AsyncIterator[Callable[..., Awaitable[Dict[str, Any]]]]:
    networks = []

    async def creator(**network_config_kwargs) -> Dict[str, Any]:
        network: aiodocker.docker.DockerNetwork = (
            await async_docker_client.networks.create(
                config={"Name": faker.uuid4(), "Driver": network_driver}
                | network_config_kwargs
            )
        )
        networks.append(network)
        return await network.show()

    yield creator
    # cleanup
    await asyncio.gather(*[network.delete() for network in networks])


@pytest.mark.parametrize(
    "network_driver, expected_found", [("bridge", False), ("overlay", True)]
)
async def test_get_network_id(
    docker_swarm,
    async_docker_client: aiodocker.Docker,
    docker_network: Callable[..., Awaitable[Dict[str, Any]]],
    mocked_logger: mock.MagicMock,
    expected_found: bool,
):
    # wrong name shall raise
    with pytest.raises(ValueError):
        await get_network_id(async_docker_client, "a_fake_network_name", mocked_logger)
    # create 1 network
    network1 = await docker_network()
    if expected_found:
        network_id = await get_network_id(
            async_docker_client, network1["Name"], mocked_logger
        )
        assert network_id == network1["Id"]
    else:
        with pytest.raises(ValueError):
            await get_network_id(async_docker_client, network1["Name"], mocked_logger)


def test_create_service_parameters(minimal_config: None, faker: Faker):
    settings = AppSettings()
    worker_env = faker.pydict()
    service_name = faker.name()
    network_id = faker.uuid4()
    secrets = []
    cmd = faker.pystr()
    service_parameters = create_service_config(
        settings=settings,
        service_env=worker_env,
        service_name=service_name,
        network_id=network_id,
        service_secrets=secrets,
        cmd=cmd,
    )
    assert service_parameters
    assert service_parameters["name"] == service_name
    assert network_id in service_parameters["networks"]
    for env_key, env_value in worker_env.items():
        assert env_key in service_parameters["task_template"]["ContainerSpec"]["Env"]
        assert (
            service_parameters["task_template"]["ContainerSpec"]["Env"][env_key]
            == env_value
        )
    assert service_parameters["task_template"]["ContainerSpec"]["Command"] == cmd


@pytest.fixture
def fake_secret_file(tmp_path) -> Path:
    fake_secret_file = Path(tmp_path / "fake_file")
    fake_secret_file.write_text("Hello I am a secret file")
    assert fake_secret_file.exists()
    return fake_secret_file


@pytest.fixture
async def fake_cluster(faker: Faker) -> Cluster:
    return Cluster(id=faker.uuid4(), name=faker.pystr(), status=JobStatus.CREATED)


async def test_create_or_update_docker_secrets(
    docker_swarm,
    async_docker_client: aiodocker.Docker,
    fake_secret_file: Path,
    fake_cluster: Cluster,
    faker: Faker,
):
    file_original_size = fake_secret_file.stat().st_size
    # check secret creation
    secret_name = faker.pystr()
    worker_env = ["some_fake_env_related_to_secret_for_worker"]
    scheduler_env = ["some_fake_env_related_to_secret_for_scheduler"]
    created_secret: DockerSecret = await create_or_update_secret(
        async_docker_client,
        secret_name,
        fake_cluster,
        file_path=fake_secret_file,
    )
    list_of_secrets = await async_docker_client.secrets.list()
    assert len(list_of_secrets) == 1
    secret = list_of_secrets[0]
    assert created_secret.secret_id == secret["ID"]
    inspected_secret = await async_docker_client.secrets.inspect(secret["ID"])

    assert created_secret.secret_name == inspected_secret["Spec"]["Name"]
    assert "cluster_id" in inspected_secret["Spec"]["Labels"]
    assert inspected_secret["Spec"]["Labels"]["cluster_id"] == fake_cluster.id
    assert "cluster_name" in inspected_secret["Spec"]["Labels"]
    assert inspected_secret["Spec"]["Labels"]["cluster_name"] == fake_cluster.name

    # check update of secret
    fake_secret_file.write_text("some additional stuff in the file")
    assert fake_secret_file.stat().st_size != file_original_size

    updated_secret: DockerSecret = await create_or_update_secret(
        async_docker_client,
        secret_name,
        fake_cluster,
        file_path=fake_secret_file,
    )
    assert updated_secret.secret_id != created_secret.secret_id
    secrets = await async_docker_client.secrets.list()
    assert len(secrets) == 1
    updated_secret = secrets[0]
    assert updated_secret != created_secret

    # create a second one
    secret_name2 = faker.pystr()
    created_secret: DockerSecret = await create_or_update_secret(
        async_docker_client,
        secret_name2,
        fake_cluster,
        file_path=fake_secret_file,
    )
    secrets = await async_docker_client.secrets.list()
    assert len(secrets) == 2

    # test deletion
    await delete_secrets(async_docker_client, fake_cluster)
    secrets = await async_docker_client.secrets.list()
    assert len(secrets) == 0
