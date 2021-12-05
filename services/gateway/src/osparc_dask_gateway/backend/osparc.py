import asyncio
import json
import os
from typing import List
from urllib.parse import urlsplit, urlunsplit

from aiodocker import Docker
from aiodocker.exceptions import DockerContainerError, DockerError
from aiodocker.volumes import DockerVolume
from dask_gateway_server.backends.base import ClusterConfig
from dask_gateway_server.backends.db_base import Cluster, Worker
from dask_gateway_server.backends.local import LocalBackend
from dask_gateway_server.traitlets import Type

from .settings import AppSettings

__all__ = ("OsparcClusterConfig", "OsparcBackend", "UnsafeOsparcBackend")


def in_docker():
    """Returns: True if running in a Docker container, else False"""
    with open("/proc/1/cgroup", "rt") as ifh:
        return "docker" in ifh.read()


async def _is_task_running(docker_client: Docker, service_name: str, logger) -> bool:
    tasks = await docker_client.tasks.list(filters={"service": service_name})
    tasks_current_state = [task["Status"]["State"] for task in tasks]
    logger.info(
        "%s current service task states are %s", service_name, f"{tasks_current_state=}"
    )
    num_running = sum(current == "running" for current in tasks_current_state)
    return num_running == 1


class OsparcClusterConfig(ClusterConfig):
    """Dask cluster configuration options when running as osparc backend"""

    pass


class OsparcBackend(LocalBackend):
    """A cluster backend that launches osparc workers.

    Workers are spawned as services in a docker swarm
    """

    cluster_config_class = Type(
        "osparc_dask_gateway.backend.osparc.OsparcClusterConfig",
        klass="dask_gateway_server.backends.base.ClusterConfig",
        help="The cluster config class to use",
        config=True,
    )

    default_host = "0.0.0.0"

    containers = {}  # keeping track of created containers

    settings: AppSettings = AppSettings()

    async def do_setup(self):
        await super().do_setup()
        self.log.info(
            "Osparc-Dask-Gateway application settings:\n%s",
            self.settings.json(indent=2),
        )

    async def do_start_worker(self, worker: Worker):
        self.log.debug("received call to start worker as %s", f"{worker=}")
        env = self.get_worker_env(worker.cluster)

        scheduler_url = urlsplit(worker.cluster.scheduler_address)
        scheduler_host = scheduler_url.netloc.split(":")[0]
        port = scheduler_url.netloc.split(":")[1]
        netloc = f"{self.settings.GATEWAY_SERVER_NAME}:{port}"
        scheduler_address = urlunsplit(scheduler_url._replace(netloc=netloc))

        db_address = f"{self.default_host}:8787"
        workdir = worker.cluster.state.get("workdir")

        nthreads, memory_limit = self.worker_nthreads_memory_limit_args(worker.cluster)

        env.update(
            {
                "DASK_SCHEDULER_HOST": scheduler_host,
                "DASK_SCHEDULER_ADDRESS": scheduler_address,
                "DASK_DASHBOARD_ADDRESS": db_address,
                # "DASK_NTHREADS": nthreads,
                # "DASK_MEMORY_LIMIT": memory_limit,
                "DASK_WORKER_NAME": f"{worker.name}",
                "SIDECAR_COMP_SERVICES_SHARED_FOLDER": self.settings.GATEWAY_WORK_FOLDER,
                "SIDECAR_COMP_SERVICES_SHARED_VOLUME_NAME": self.settings.GATEWAY_VOLUME_NAME,
                "LOG_LEVEL": self.settings.COMPUTATIONAL_SIDECAR_LOG_LEVEL,
            }
        )

        docker_image = self.settings.COMPUTATIONAL_SIDECAR_IMAGE
        workdir = worker.cluster.state.get("workdir")
        self.log.debug("workdir set as %s", f"{workdir=}")
        container_config = {}

        try:
            async with Docker() as docker_client:
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
                        "Source": self.settings.GATEWAY_VOLUME_NAME,
                        "Target": self.settings.GATEWAY_WORK_FOLDER,
                        "Type": "volume",
                        "ReadOnly": False,
                    },
                ]

                container_config = {
                    "Env": env,
                    "Image": docker_image,
                    "Init": True,
                    "Mounts": mounts,
                }

                network_name = self.settings.GATEWAY_WORKERS_NETWORK

                # try to find the network name (usually named STACKNAME_default)
                networks = [
                    x
                    for x in (await docker_client.networks.list())
                    if "swarm" in x["Scope"] and network_name in x["Name"]
                ]
                if not networks or len(networks) > 1:
                    self.log.error(
                        "Swarm network name is not configured, found following networks "
                        "(if there is more then 1 network, remove the one which has no "
                        f"containers attached and all is fixed): {networks}"
                    )
                worker_network = networks[0]
                network_name = worker_network["Name"]
                self.log.info("Attaching worker to network %s", network_name)
                network_id = worker_network["Id"]
                service_name = worker.name
                service_parameters = {
                    "name": service_name,
                    "task_template": {
                        "ContainerSpec": container_config,
                    },
                    "networks": [network_id],
                }

                self.log.info("Starting service %s", service_name)
                self.log.debug(
                    "Using parameters %s", json.dumps(service_parameters, indent=2)
                )
                service = await docker_client.services.create(**service_parameters)
                self.log.info("Service %s started: %s", service_name, f"{service=}")
                yield {"service_id": service["ID"]}

                # get the full info from docker
                service = await docker_client.services.inspect(service["ID"])
                self.log.debug(
                    "Service %s inspection: %s",
                    service_name,
                    f"{json.dumps(service, indent=2)}",
                )

                # wait until the service is started
                self.log.info(
                    "---> Service started, waiting for service %s to start...",
                    service_name,
                )
                while not await _is_task_running(
                    docker_client, service["Spec"]["Name"], self.log
                ):
                    yield {"service_id": service["ID"]}
                    await asyncio.sleep(1)

                # we are done, the service is started
                self.log.info(
                    "---> Service %s is started, and has ID %s",
                    worker.name,
                    service["ID"],
                )
                yield {"service_id": service["ID"]}

        except DockerContainerError:
            self.log.exception(
                "Error while running %s with parameters %s",
                docker_image,
                container_config,
            )
            raise
        except DockerError:
            self.log.exception(
                "Unknown error while trying to run %s with parameters %s",
                docker_image,
                container_config,
            )
            raise
        except asyncio.CancelledError:
            self.log.warn("Service creation was cancelled")
            raise

    async def do_stop_worker(self, worker: Worker):
        self.log.debug("Calling to stop worker %s", f"{worker=}")
        if service_id := worker.state.get("service_id"):
            self.log.info("Stopping service %s", f"{service_id=}")
            try:
                async with Docker() as docker_client:
                    await docker_client.services.delete(service_id)
                self.log.info("service %s stopped", f"{service_id=}")

            except DockerContainerError:
                self.log.exception(
                    "Error while stopping service with id %s", service_id
                )
        else:
            self.log.error(
                "Worker %s does not have a service id! That is not expected!", worker
            )

    async def _check_service_status(self, worker: Worker) -> bool:
        self.log.debug("checking worker status: %s", f"{worker=}")
        if service_id := worker.state.get("service_id"):
            self.log.debug("checking worker %s status", service_id)
            try:
                async with Docker() as docker_client:
                    service = await docker_client.services.inspect(service_id)
                    self.log.debug(
                        "checking worker %s associated service", f"{service=}"
                    )
                    if service:
                        service_name = service["Spec"]["Name"]
                        return await _is_task_running(
                            docker_client, service_name, self.log
                        )

            except DockerContainerError:
                self.log.exception(
                    "Error while checking container with id %s", service_id
                )
        self.log.error(
            "Worker %s does not have a service id! That is not expected!", worker
        )
        return False

    async def do_check_workers(self, workers: List[Worker]) -> List[bool]:
        self.log.debug("--> checking workers statuses: %s", f"{workers=}")
        ok = await asyncio.gather(
            *[self._check_service_status(w) for w in workers], return_exceptions=True
        )
        self.log.debug("<-- worker status returned: %s", f"{ok=}")
        return ok


class UnsafeOsparcBackend(OsparcBackend):
    """A version of OsparcBackend that doesn't set permissions.

    This provides no user separations - clusters run with the
    same level of permission as the gateway, which is fine,
    everyone is a scu
    """

    def make_preexec_fn(self, cluster: Cluster):
        workdir = cluster.state["workdir"]

        def preexec():  # pragma: nocover
            os.chdir(workdir)

        return preexec

    def set_file_permissions(self, paths, username):
        pass
