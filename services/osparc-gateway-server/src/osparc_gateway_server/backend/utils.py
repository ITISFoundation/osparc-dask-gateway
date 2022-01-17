import asyncio
import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, NamedTuple, Optional

import aiodocker
from aiodocker import Docker
from dask_gateway_server.backends.db_base import Cluster

from .settings import AppSettings

_SHARED_COMPUTATIONAL_FOLDER_IN_SIDECAR = "/home/scu/shared_computational_data"
_DASK_KEY_CERT_PATH_IN_SIDECAR = Path("/home/scu/dask-credentials")


class DockerSecret(NamedTuple):
    secret_id: str
    secret_name: str
    secret_file_name: str
    cluster: Cluster


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
    service_env: Dict[str, Any],
    service_name: str,
    network_id: str,
    service_secrets: List[DockerSecret],
    cmd: Optional[List[str]],
    labels: Dict[str, str],
) -> Dict[str, Any]:
    env = deepcopy(service_env)
    env.pop("PATH", None)
    # create the secrets array containing the TLS cert/key pair
    container_secrets = []
    for s in service_secrets:
        container_secrets.append(
            {
                "SecretName": s.secret_name,
                "SecretID": s.secret_id,
                "File": {
                    "Name": f"{_DASK_KEY_CERT_PATH_IN_SIDECAR / s.secret_file_name}",
                    "UID": "0",
                    "GID": "0",
                    "Mode": 0x777,
                },
            }
        )
        env_updates = {}
        for env_name, env_value in env.items():
            if env_value == s.secret_file_name:
                env_updates[
                    env_name
                ] = f"{_DASK_KEY_CERT_PATH_IN_SIDECAR / s.secret_file_name}"
        env.update(env_updates)
    mounts = [
        # docker socket needed to use the docker api
        {
            "Source": "/var/run/docker.sock",
            "Target": "/var/run/docker.sock",
            "Type": "bind",
            "ReadOnly": True,
        },
        # the sidecar data data is stored in a volume
        {
            "Source": settings.COMPUTATIONAL_SIDECAR_VOLUME_NAME,
            "Target": _SHARED_COMPUTATIONAL_FOLDER_IN_SIDECAR,
            "Type": "volume",
            "ReadOnly": False,
        },
    ]

    container_config = {
        "Env": env,
        "Image": settings.COMPUTATIONAL_SIDECAR_IMAGE,
        "Init": True,
        "Mounts": mounts,
        "Secrets": container_secrets,
        # "Command": ["ls", "-tlah", f"{_DASK_KEY_CERT_PATH_IN_SIDECAR}"],
    }
    if cmd:
        container_config["Command"] = cmd
    return {
        "name": service_name,
        "labels": labels,
        "task_template": {
            "ContainerSpec": container_config,
            "RestartPolicy": {"Condition": "on-failure"},
        },
        "networks": [network_id],
        # "mode": {"Global": {}},
    }


async def create_or_update_secret(
    docker_client: aiodocker.Docker,
    target_file_name: str,
    cluster: Cluster,
    *,
    file_path: Optional[Path] = None,
    secret_data: Optional[str] = None,
) -> DockerSecret:
    if file_path is None and secret_data is None:
        raise ValueError(
            f"Both {file_path=} and {secret_data=} are empty, that is not allowed"
        )
    data = secret_data
    if not data and file_path:
        data = file_path.read_text()

    docker_secret_name = f"{target_file_name}_{cluster.id}"

    secrets = await docker_client.secrets.list(filters={"name": docker_secret_name})
    if secrets:
        # we must first delete it as only labels may be updated
        secret = secrets[0]
        await docker_client.secrets.delete(secret["ID"])
    secret = await docker_client.secrets.create(
        name=docker_secret_name,
        data=data,
        labels={"cluster_id": f"{cluster.id}", "cluster_name": f"{cluster.name}"},
    )
    return DockerSecret(
        secret_id=secret["ID"],
        secret_name=docker_secret_name,
        secret_file_name=target_file_name,
        cluster=cluster,
    )


async def delete_secrets(docker_client: aiodocker.Docker, cluster: Cluster):
    secrets = await docker_client.secrets.list(
        filters={"label": f"cluster_id={cluster.id}"}
    )
    await asyncio.gather(*[docker_client.secrets.delete(s["ID"]) for s in secrets])


async def start_service(
    docker_client: aiodocker.Docker,
    settings: AppSettings,
    logger: logging.Logger,
    service_name: str,
    base_env: Dict[str, str],
    cluster_secrets: List[DockerSecret],
    cmd: Optional[List[str]],
    labels: Dict[str, str],
) -> AsyncGenerator[Dict[str, Any], None]:
    service_parameters = {}
    try:
        assert settings.COMPUTATIONAL_SIDECAR_LOG_LEVEL  # nosec
        env = deepcopy(base_env)
        env.update(
            {
                "SIDECAR_COMP_SERVICES_SHARED_FOLDER": _SHARED_COMPUTATIONAL_FOLDER_IN_SIDECAR,
                "SIDECAR_COMP_SERVICES_SHARED_VOLUME_NAME": settings.COMPUTATIONAL_SIDECAR_VOLUME_NAME,
                "LOG_LEVEL": settings.COMPUTATIONAL_SIDECAR_LOG_LEVEL,
            }
        )

        # find service parameters
        network_id = await get_network_id(
            docker_client, settings.GATEWAY_WORKERS_NETWORK, logger
        )
        service_parameters = create_service_config(
            settings, env, service_name, network_id, cluster_secrets, cmd, labels=labels
        )

        # start service
        logger.info("Starting service %s", service_name)
        logger.debug("Using parameters %s", json.dumps(service_parameters, indent=2))
        service = await docker_client.services.create(**service_parameters)
        logger.info("Service %s started: %s", service_name, f"{service=}")
        yield {"service_id": service["ID"]}

        # get the full info from docker
        service = await docker_client.services.inspect(service["ID"])
        logger.debug(
            "Service %s inspection: %s",
            service_name,
            f"{json.dumps(service, indent=2)}",
        )

        # wait until the service is started
        logger.info(
            "---> Service started, waiting for service %s to run...",
            service_name,
        )
        while not await is_service_task_running(
            docker_client, service["Spec"]["Name"], logger
        ):
            yield {"service_id": service["ID"]}
            await asyncio.sleep(1)

        # we are done, the service is started
        logger.info(
            "---> Service %s is started, and has ID %s",
            service["Spec"]["Name"],
            service["ID"],
        )
        yield {"service_id": service["ID"]}

    except (aiodocker.DockerContainerError, aiodocker.DockerError):
        logger.exception(
            "Unexpected Error while running container with parameters %s",
            json.dumps(service_parameters, indent=2),
        )
        raise
    except asyncio.CancelledError:
        logger.warn("Service creation was cancelled")
        raise


async def stop_service(
    docker_client: aiodocker.Docker, service_id: str, logger: logging.Logger
) -> None:
    logger.info("Stopping service %s", f"{service_id}")
    try:
        await docker_client.services.delete(service_id)
        logger.info("service %s stopped", f"{service_id=}")

    except aiodocker.DockerContainerError:
        logger.exception("Error while stopping service with id %s", f"{service_id=}")
