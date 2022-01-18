import asyncio
from typing import AsyncIterator

import aiodocker
import pytest

pytest_plugins = ["pytest_simcore.repository_paths", "pytest_simcore.docker_swarm"]


@pytest.fixture
async def dask_gateway_stack_deployed_services(loop, docker_swarm):
    process = await asyncio.create_subprocess_exec("make", "up-prod")
    await process.wait()
    yield
    process = await asyncio.create_subprocess_exec("make", "down")
    await process.wait()


async def test_deployment(dask_gateway_stack_deployed_services):
    ...
