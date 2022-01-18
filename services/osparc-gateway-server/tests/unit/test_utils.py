# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name

from copy import deepcopy
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict
from unittest import mock

import aiodocker
import pytest
from dask_gateway_server.backends.db_base import Cluster, JobStatus
from faker import Faker
from osparc_gateway_server.backend.settings import AppSettings
from osparc_gateway_server.backend.utils import (
    _DASK_KEY_CERT_PATH_IN_SIDECAR,
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


async def test_get_network_id(
    docker_swarm,
    async_docker_client: aiodocker.Docker,
    docker_network: Callable[..., Awaitable[Dict[str, Any]]],
    mocked_logger: mock.MagicMock,
):
    # wrong name shall raise
    with pytest.raises(ValueError):
        await get_network_id(async_docker_client, "a_fake_network_name", mocked_logger)
    # create 1 bridge network, shall raise when looking for it
    bridge_network = await docker_network(**{"Driver": "bridge"})
    with pytest.raises(ValueError):
        await get_network_id(async_docker_client, bridge_network["Name"], mocked_logger)
    # create 1 overlay network
    overlay_network = await docker_network()
    network_id = await get_network_id(
        async_docker_client, overlay_network["Name"], mocked_logger
    )
    assert network_id == overlay_network["Id"]

    # create a second overlay network with the same name, shall raise on creation, so not possible
    with pytest.raises(aiodocker.exceptions.DockerError):
        await docker_network(**{"Name": overlay_network["Name"]})
    assert (
        True
    ), "If it is possible to have 2 networks with the same name, this must be handled"


@pytest.fixture
async def fake_cluster(faker: Faker) -> Cluster:
    return Cluster(id=faker.uuid4(), name=faker.pystr(), status=JobStatus.CREATED)


async def test_create_service_config(
    docker_swarm,
    async_docker_client: aiodocker.Docker,
    minimal_config: None,
    faker: Faker,
    fake_cluster: Cluster,
):
    # let's create some fake service config
    settings = AppSettings()  # type: ignore
    service_env = faker.pydict()
    service_name = faker.name()
    network_id = faker.uuid4()
    cmd = faker.pystr()
    fake_labels = faker.pydict()

    # create a second one
    secrets = [
        await create_or_update_secret(
            async_docker_client,
            faker.file_path(),
            fake_cluster,
            secret_data=faker.text(),
        )
        for n in range(3)
    ]

    assert len(await async_docker_client.secrets.list()) == 3

    # we shall have some env that tells the service where the secret is located
    expected_service_env = deepcopy(service_env)
    for s in secrets:
        fake_env_key = faker.pystr()
        service_env[fake_env_key] = s.secret_file_name
        expected_service_env[
            fake_env_key
        ] = f"{_DASK_KEY_CERT_PATH_IN_SIDECAR / Path(s.secret_file_name).name}"

    service_parameters = create_service_config(
        settings=settings,
        service_env=service_env,
        service_name=service_name,
        network_id=network_id,
        service_secrets=secrets,
        cmd=cmd,
        labels=fake_labels,
    )
    assert service_parameters
    assert service_parameters["name"] == service_name
    assert network_id in service_parameters["networks"]

    for env_key, env_value in expected_service_env.items():
        assert env_key in service_parameters["task_template"]["ContainerSpec"]["Env"]
        assert (
            service_parameters["task_template"]["ContainerSpec"]["Env"][env_key]
            == env_value
        )
    assert service_parameters["task_template"]["ContainerSpec"]["Command"] == cmd
    assert service_parameters["labels"] == fake_labels
    assert len(service_parameters["task_template"]["ContainerSpec"]["Secrets"]) == 3
    for service_secret, original_secret in zip(
        service_parameters["task_template"]["ContainerSpec"]["Secrets"], secrets
    ):
        assert service_secret["SecretName"] == original_secret.secret_name
        assert service_secret["SecretID"] == original_secret.secret_id
        assert (
            service_secret["File"]["Name"]
            == f"{_DASK_KEY_CERT_PATH_IN_SIDECAR / Path(original_secret.secret_file_name).name}"
        )


@pytest.fixture
def fake_secret_file(tmp_path) -> Path:
    fake_secret_file = Path(tmp_path / "fake_file")
    fake_secret_file.write_text("Hello I am a secret file")
    assert fake_secret_file.exists()
    return fake_secret_file


async def test_create_or_update_docker_secrets_with_invalid_call_raises(
    docker_swarm,
    async_docker_client: aiodocker.Docker,
    fake_cluster: Cluster,
    faker: Faker,
):
    with pytest.raises(ValueError):
        await create_or_update_secret(
            async_docker_client,
            faker.file_path(),
            fake_cluster,
        )


async def test_create_or_update_docker_secrets(
    docker_swarm,
    async_docker_client: aiodocker.Docker,
    fake_secret_file: Path,
    fake_cluster: Cluster,
    faker: Faker,
):
    file_original_size = fake_secret_file.stat().st_size
    # check secret creation
    secret_target_file_name = faker.file_path()
    created_secret: DockerSecret = await create_or_update_secret(
        async_docker_client,
        secret_target_file_name,
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
        secret_target_file_name,
        fake_cluster,
        file_path=fake_secret_file,
    )
    assert updated_secret.secret_id != created_secret.secret_id
    secrets = await async_docker_client.secrets.list()
    assert len(secrets) == 1
    updated_secret = secrets[0]
    assert updated_secret != created_secret

    # create a second one
    secret_target_file_name2 = faker.file_path()
    created_secret: DockerSecret = await create_or_update_secret(
        async_docker_client,
        secret_target_file_name2,
        fake_cluster,
        secret_data=faker.text(),
    )
    secrets = await async_docker_client.secrets.list()
    assert len(secrets) == 2

    # test deletion
    await delete_secrets(async_docker_client, fake_cluster)
    secrets = await async_docker_client.secrets.list()
    assert len(secrets) == 0
