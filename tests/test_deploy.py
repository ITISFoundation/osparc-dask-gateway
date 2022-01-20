import asyncio
import json
import socket
import sys
from copy import deepcopy
from pathlib import Path
from re import L
from typing import AsyncIterator

import aiohttp
import dask_gateway
import pytest
from faker import Faker
from tenacity._asyncio import AsyncRetrying
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_fixed

pytest_plugins = ["pytest_simcore.repository_paths", "pytest_simcore.docker_swarm"]


def _get_this_computer_ip() -> str:
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
async def aiohttp_client() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


## current directory
CURRENT_DIR = Path(sys.argv[0] if __name__ == "__main__" else __file__).resolve().parent
WILDCARD = "services/osparc-gateway-server/README.md"
ROOT = Path("/")


# overrides pytest-simcore fixture such that this folder is legal
@pytest.fixture(scope="session")
def osparc_gateway_server_root_dir(request) -> Path:
    """osparc-simcore repo root dir"""
    test_dir = Path(request.session.fspath)  # expected test dir in simcore

    root_dir = CURRENT_DIR
    for start_dir in (CURRENT_DIR, test_dir):
        root_dir = start_dir
        while not any(root_dir.glob(WILDCARD)) and root_dir != ROOT:
            root_dir = root_dir.parent

        if root_dir != ROOT:
            break

    msg = (
        f"'{root_dir}' does not look like the git root directory of osparc-dask-gateway"
    )

    assert root_dir != ROOT, msg
    assert root_dir.exists(), msg
    assert any(root_dir.glob(WILDCARD)), msg
    assert any(root_dir.glob(".git")), msg

    return root_dir


@pytest.fixture(scope="session")
def dask_gateway_entrypoint() -> str:
    return f"http://{_get_this_computer_ip()}:8000"


@pytest.fixture(scope="session")
def dask_gateway_password() -> str:
    return "asdf"


@pytest.fixture
async def dask_gateway_stack_deployed_services(
    osparc_gateway_server_root_dir: Path,
    loop: asyncio.AbstractEventLoop,
    docker_swarm,
    aiohttp_client: aiohttp.ClientSession,
    dask_gateway_entrypoint: str,
):
    print("--> Deploying osparc-dask-gateway stack...")
    process = await asyncio.create_subprocess_exec(
        "make",
        "up-prod",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=osparc_gateway_server_root_dir,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, f"Unexpected error while deploying stack:\n{stderr}"
    print(f"{stdout}")
    print("--> osparc-dask-gateway stack deployed.")
    healtcheck_endpoint = f"{dask_gateway_entrypoint}/api/health"
    async for attempt in AsyncRetrying(
        reraise=True, wait=wait_fixed(1), stop=stop_after_delay(60)
    ):
        with attempt:
            print(
                f"--> Connecting to {healtcheck_endpoint}, "
                f"attempt {attempt.retry_state.attempt_number}...",
            )
            response = await aiohttp_client.get(healtcheck_endpoint)
            response.raise_for_status()
        print(
            f"--> Connection to gateway server succeeded."
            f" [{json.dumps(attempt.retry_state.retry_object.statistics)}]",
        )

    yield
    print("<-- Stopping osparc-dask-gateway stack...")
    process = await asyncio.create_subprocess_exec(
        "make",
        "down",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=osparc_gateway_server_root_dir,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, f"Unexpected error while deploying stack:\n{stderr}"
    print(f"{stdout}")
    print("<-- osparc-dask-gateway stack stopped.")


async def test_deployment(
    dask_gateway_stack_deployed_services,
    dask_gateway_entrypoint: str,
    faker: Faker,
    dask_gateway_password: str,
):
    gateway = dask_gateway.Gateway(
        address=dask_gateway_entrypoint,
        auth=dask_gateway.BasicAuth(faker.pystr(), dask_gateway_password),
    )

    cluster = (
        gateway.new_cluster()
    )  # when returning we do have a new cluster (e.g. a new scheduler)

    _NUM_WORKERS = 2
    cluster.scale(
        _NUM_WORKERS
    )  # when returning we are in the process of creating the workers

    # now wait until we get the workers
    workers = None
    async for attempt in AsyncRetrying(
        reraise=True, wait=wait_fixed(1), stop=stop_after_delay(60)
    ):
        with attempt:
            print(
                f"--> Waiting to have {_NUM_WORKERS} running,"
                f" attempt {attempt.retry_state.attempt_number}...",
            )
            assert "workers" in cluster.scheduler_info
            assert len(cluster.scheduler_info["workers"]) == _NUM_WORKERS
            workers = deepcopy(cluster.scheduler_info["workers"])
            print(
                f"!-- {_NUM_WORKERS} are running,"
                f" [{json.dumps(attempt.retry_state.retry_object.statistics)}]",
            )

    # now check all this is stable
    _SECONDS_STABLE = 6
    for n in range(_SECONDS_STABLE):
        # NOTE: the scheduler_info gets auto-udpated by the dask-gateway internals
        assert workers == cluster.scheduler_info["workers"]
        await asyncio.sleep(1)