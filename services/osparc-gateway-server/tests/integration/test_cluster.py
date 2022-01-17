# pylint: disable=unused-argument
# pylint: disable=redefined-outer-name

import asyncio
from typing import Any, Awaitable, Callable, Dict, List

import pytest
from _dask_helpers import DaskGatewayServer
from _host_helpers import get_this_computer_ip
from _pytest.monkeypatch import MonkeyPatch
from aiodocker import Docker
from dask_gateway import Gateway
from faker import Faker
from tenacity._asyncio import AsyncRetrying
from tenacity.stop import stop_after_delay
from tenacity.wait import wait_fixed

PASSWORD = "asdf"
TMP_FOLDER = "/tmp/gateway"


@pytest.fixture
def minimal_config(
    loop: asyncio.AbstractEventLoop,
    docker_swarm,
    monkeypatch: MonkeyPatch,
    faker: Faker,
):
    monkeypatch.setenv("GATEWAY_WORKERS_NETWORK", faker.pystr())
    monkeypatch.setenv("GATEWAY_SERVER_NAME", get_this_computer_ip())
    monkeypatch.setenv("COMPUTATIONAL_SIDECAR_VOLUME_NAME", faker.pystr())
    # TODO: this should be parametrized to check all possible dask-sidecar images (master, staging, prod)
    monkeypatch.setenv(
        "COMPUTATIONAL_SIDECAR_IMAGE",
        "itisfoundation/dask-sidecar:master-github-latest",
    )
    monkeypatch.setenv("COMPUTATIONAL_SIDECAR_LOG_LEVEL", "DEBUG")


@pytest.fixture
async def gateway_worker_network(
    local_dask_gateway_server: DaskGatewayServer,
    docker_network: Callable[[str], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    network = await docker_network(
        local_dask_gateway_server.server.backend.settings.GATEWAY_WORKERS_NETWORK
    )
    return network


async def assert_services_stability(docker_client: Docker, service_name: str):
    list_services = await docker_client.services.list(filters={"name": service_name})
    assert (
        len(list_services) == 1
    ), f"{service_name} is missing from the expected services in {list_services}"
    _SECONDS_STABLE = 10
    print(f"--> {service_name} is up, now checking it is stable {_SECONDS_STABLE}s")

    async def _check_stability(service: Dict[str, Any]):
        inspected_service = await docker_client.services.inspect(service["ID"])
        # we ensure the service remains stable for _SECONDS_STABLE seconds (e.g. only one task runs)

        print(
            f"--> checking {_SECONDS_STABLE} seconds for stability of service {inspected_service['Spec']['Name']=}"
        )
        for n in range(_SECONDS_STABLE):
            service_tasks = await docker_client.tasks.list(
                filters={"service": inspected_service["Spec"]["Name"]}
            )
            assert (
                len(service_tasks) == 1
            ), f"The service is not stable it shows {service_tasks}"
            print(f"the service {service_name} is stable after {n} seconds...")
            await asyncio.sleep(1)
        print(f"service stable!!")

    await asyncio.gather(*[_check_stability(s) for s in list_services])


async def _wait_for_cluster_services_and_secrets(
    async_docker_client: Docker,
    num_services: int,
    num_secrets: int,
    timeout_s: int = 10,
) -> List[Dict[str, Any]]:
    async for attempt in AsyncRetrying(
        reraise=True, wait=wait_fixed(1), stop=stop_after_delay(timeout_s)
    ):
        with attempt:
            list_services = await async_docker_client.services.list()
            print(
                f"--> list of services after {attempt.retry_state.attempt_number}s: {list_services=}, expected {num_services=}"
            )
            assert len(list_services) == num_services
            # as the secrets
            list_secrets = await async_docker_client.secrets.list()
            print(
                f"--> list of secrets after {attempt.retry_state.attempt_number}s: {list_secrets=}, expected {num_secrets}"
            )
            assert len(list_secrets) == num_secrets
            return list_services
    # needed for pylint
    raise AssertionError("Invalid call to _wait_for_cluster_services_and_secrets")


async def test_clusters_start_stop(
    minimal_config,
    gateway_worker_network,
    gateway_client: Gateway,
    async_docker_client: Docker,
):
    """Each cluster is made of 1 scheduler + X number of sidecars (with 0<=X<infinite)"""
    # No currently running clusters
    clusters = await gateway_client.list_clusters()
    assert clusters == []

    # create one cluster
    async with gateway_client.new_cluster() as cluster1:
        clusters = await gateway_client.list_clusters()
        assert len(clusters) == 1
        assert clusters[0].name == cluster1.name
        print(f"found cluster: {clusters[0]=}")
        list_services = await _wait_for_cluster_services_and_secrets(
            async_docker_client, num_services=1, num_secrets=2
        )
        # now we should have a dask_scheduler happily running in the host
        assert len(list_services) == 1
        assert list_services[0]["Spec"]["Name"] == "cluster_1_scheduler"
        assert list_services[0]["Spec"]["Labels"] == {"cluster_id": "1"}
        # there should be one service and a stable one
        await assert_services_stability(async_docker_client, "cluster_1_scheduler")

        # let's create a second cluster
        async with gateway_client.new_cluster() as cluster2:
            clusters = await gateway_client.list_clusters()
            # now we should have 2 clusters (e.g. 2 dask-schedulers)
            assert len(clusters) == 2
            assert clusters[1].name == cluster2.name
            list_services = await _wait_for_cluster_services_and_secrets(
                async_docker_client, num_services=2, num_secrets=4
            )

            # this list is not ordered
            assert len(list_services) == 2
            for s in list_services:
                assert list_services[1]["Spec"]["Name"] in [
                    "cluster_1_scheduler",
                    "cluster_2_scheduler",
                ]
                assert list_services[1]["Spec"]["Labels"] in [
                    {"cluster_id": "1"},
                    {"cluster_id": "2"},
                ]
            # the new scheduler should be as stable as a rock!
            await assert_services_stability(async_docker_client, "cluster_2_scheduler")
        # the second cluster is now closed
        list_services = await _wait_for_cluster_services_and_secrets(
            async_docker_client, num_services=1, num_secrets=2
        )
        assert list_services[0]["Spec"]["Name"] == "cluster_1_scheduler"
        assert list_services[0]["Spec"]["Labels"] == {"cluster_id": "1"}
    # now both clusters are gone
    clusters = await gateway_client.list_clusters()
    assert not clusters
    # check the services are all gone
    await _wait_for_cluster_services_and_secrets(
        async_docker_client, num_services=0, num_secrets=0
    )


async def test_cluster_scale(
    minimal_config: None,
    gateway_client: Gateway,
    gateway_worker_network: Dict[str, Any],
    async_docker_client: Docker,
):

    # No currently running clusters
    clusters = await gateway_client.list_clusters()
    assert clusters == []

    # create a cluster
    async with gateway_client.new_cluster() as cluster:

        # Cluster is now present in list
        clusters = await gateway_client.list_clusters()
        assert len(clusters)
        assert clusters[0].name == cluster.name
        import pdb

        pdb.set_trace()
        # let's get some workers
        _NUM_WORKERS = 5
        await cluster.scale(_NUM_WORKERS)
        # wait for them to be up
        list_services = await _wait_for_cluster_services_and_secrets(
            async_docker_client,
            num_services=1 + _NUM_WORKERS,
            num_secrets=2,
            timeout_s=60,
        )
        # let's check everything is stable
        await asyncio.gather(
            *[
                assert_services_stability(async_docker_client, s["Spec"]["Name"])
                for s in list_services
            ]
        )
        # compute some stuff
        async with cluster.get_client(set_as_default=False) as client:
            res = await client.submit(lambda x: x + 1, 1)  # type: ignore
            assert res == 2

        import pdb

        pdb.set_trace()
        # Scale down
        await cluster.scale(1)
        # wait for the scaling to happen
        list_services = await _wait_for_cluster_services_and_secrets(
            async_docker_client, num_services=1 + 1, num_secrets=2, timeout_s=60
        )
        # let's check everything is stable
        await asyncio.gather(
            *[
                assert_services_stability(async_docker_client, s["Spec"]["Name"])
                for s in list_services
            ]
        )

        # Can still compute
        async with cluster.get_client(set_as_default=False) as client:
            res = await client.submit(lambda x: x + 1, 1)  # type: ignore
            assert res == 2

    # Shutdown the cluster
    # wait for the scaling to happen
    list_services = await _wait_for_cluster_services_and_secrets(
        async_docker_client, num_services=0, num_secrets=0, timeout_s=60
    )


@pytest.mark.skip("not ready")
async def test_multiple_clusters(
    minimal_config,
    gateway_client: Gateway,
    gateway_worker_network: Dict[str, Any],
    async_docker_client: Docker,
):
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
            await assert_services_stability(async_docker_client, 3)

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
            await assert_services_stability(async_docker_client, 0)
