import logging
from copy import deepcopy
from typing import Any, Dict

from aiodocker import Docker

from .settings import AppSettings


async def is_service_task_running(
    docker_client: Docker, service_name: str, logger: logging.Logger
) -> bool:
    tasks = await docker_client.tasks.list(filters={"service": service_name})
    tasks_current_state = [task["Status"]["State"] for task in tasks]
    logger.info(
        "%s current service task states are %s", service_name, f"{tasks_current_state=}"
    )
    num_running = sum(current == "running" for current in tasks_current_state)
    return num_running == 1


async def get_network_id(
    docker_client: Docker, network_name: str, logger: logging.Logger
) -> str:
    # try to find the network name (usually named STACKNAME_default)
    logger.debug("finding network id for network %s", f"{network_name=}")
    networks = [
        x
        for x in (await docker_client.networks.list())
        if "swarm" in x["Scope"] and network_name == x["Name"]
    ]
    logger.debug(f"found the following swarm networks: {networks=}")
    if not networks:
        raise ValueError(f"network {network_name} not found")
    if len(networks) > 1:
        raise ValueError(
            f"network {network_name} is ambiguous, too many network founds: {networks=}"
        )
    logger.debug("found a network %s", f"{networks[0]=}")
    assert "Id" in networks[0]  # nosec
    assert isinstance(networks[0]["Id"], str)  # nosec
    return networks[0]["Id"]


def create_service_config(
    settings: AppSettings,
    worker_env: Dict[str, Any],
    service_name: str,
    network_id: str,
    scheduler_address: str,
) -> Dict[str, Any]:
    env = deepcopy(worker_env)
    env.update(
        {
            "DASK_SCHEDULER_URL": scheduler_address,
            "DASK_SCHEDULER_HOST": "",
            # "DASK_NTHREADS": nthreads,
            # "DASK_MEMORY_LIMIT": memory_limit,
            # "DASK_WORKER_NAME": service_name,
            "SIDECAR_COMP_SERVICES_SHARED_FOLDER": "/home/scu/computational_data",
            "SIDECAR_COMP_SERVICES_SHARED_VOLUME_NAME": settings.COMPUTATIONAL_SIDECAR_VOLUME_NAME,
            "LOG_LEVEL": settings.COMPUTATIONAL_SIDECAR_LOG_LEVEL,
        }
    )
    mounts = [
        # docker socket needed to use the docker api
        {
            "Source": "/var/run/docker.sock",
            "Target": "/var/run/docker.sock",
            "Type": "bind",
            "ReadOnly": True,
        },
        # the workder data is stored in a volume
        {
            "Source": settings.GATEWAY_VOLUME_NAME,
            "Target": settings.GATEWAY_WORK_FOLDER,
            "Type": "volume",
            "ReadOnly": False,
        },
        # the sidecar data data is stored in a volume
        {
            "Source": settings.COMPUTATIONAL_SIDECAR_VOLUME_NAME,
            "Target": "/home/scu/computational_data",
            "Type": "volume",
            "ReadOnly": False,
        },
    ]

    container_config = {
        "Env": env,
        "Image": settings.COMPUTATIONAL_SIDECAR_IMAGE,
        "Init": True,
        "Mounts": mounts,
    }
    return {
        "name": service_name,
        "task_template": {
            "ContainerSpec": container_config,
            "RestartPolicy": {"Condition": "on-failure"},
        },
        "networks": [network_id],
        # "mode": {"Global": {}},
    }