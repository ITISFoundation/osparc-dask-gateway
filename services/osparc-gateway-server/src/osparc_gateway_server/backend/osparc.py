import asyncio
import json
import os
from typing import Any, AsyncGenerator, Callable, Dict, List
from urllib.parse import urlsplit, urlunsplit

from aiodocker import Docker
from aiodocker.exceptions import DockerContainerError, DockerError
from dask_gateway_server.backends.base import ClusterConfig
from dask_gateway_server.backends.db_base import Cluster, Worker
from dask_gateway_server.backends.local import LocalBackend
from dask_gateway_server.traitlets import Type

from .settings import AppSettings
from .utils import create_service_config, get_network_id, is_service_task_running

__all__ = ("OsparcClusterConfig", "OsparcBackend", "UnsafeOsparcBackend")


class OsparcClusterConfig(ClusterConfig):
    """Dask cluster configuration options when running as osparc backend"""


class OsparcBackend(LocalBackend):
    """A cluster backend that launches osparc workers.

    Workers are spawned as services in a docker swarm
    """

    cluster_config_class = Type(
        "osparc_gateway_server.backend.osparc.OsparcClusterConfig",
        klass="dask_gateway_server.backends.base.ClusterConfig",
        help="The cluster config class to use",
        config=True,
    )

    default_host = "0.0.0.0"
    # worker_start_timeout = 120

    settings: AppSettings
    docker_client: Docker

    async def do_setup(self) -> None:
        await super().do_setup()
        self.settings = AppSettings()
        self.docker_client = Docker()
        self.log.info(
            "osparc-gateway-server application settings:\n%s",
            self.settings.json(indent=2),
        )

    async def do_cleanup(self) -> None:
        await super().do_cleanup()
        await self.docker_client.close()

    # async def do_start_cluster(self, cluster) -> AsyncGenerator[Dict[str, Any], None]:
    #     return await super().do_start_cluster(cluster)

    async def do_start_worker(
        self, worker: Worker
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self.log.debug("received call to start worker as %s", f"{worker=}")

        scheduler_url = urlsplit(worker.cluster.scheduler_address)
        port = scheduler_url.netloc.split(":")[1]
        netloc = f"{self.settings.GATEWAY_SERVER_NAME}:{port}"
        scheduler_address = urlunsplit(scheduler_url._replace(netloc=netloc))

        workdir = worker.cluster.state.get("workdir")

        workdir = worker.cluster.state.get("workdir")
        self.log.debug("workdir set as %s", f"{workdir=}")

        service_parameters = None
        try:
            # find service parameters
            network_id = await get_network_id(
                self.docker_client, self.settings.GATEWAY_WORKERS_NETWORK, self.log
            )
            service_name = worker.name
            service_parameters = create_service_config(
                self.settings,
                self.get_worker_env(worker.cluster),
                service_name,
                network_id,
                scheduler_address,
            )

            # start service
            self.log.info("Starting service %s", service_name)
            self.log.debug(
                "Using parameters %s", json.dumps(service_parameters, indent=2)
            )
            service = await self.docker_client.services.create(**service_parameters)
            self.log.info("Service %s started: %s", service_name, f"{service=}")
            yield {"service_id": service["ID"]}

            # get the full info from docker
            service = await self.docker_client.services.inspect(service["ID"])
            self.log.debug(
                "Service %s inspection: %s",
                service_name,
                f"{json.dumps(service, indent=2)}",
            )

            # wait until the service is started
            self.log.info(
                "---> Service started, waiting for service %s to run...",
                service_name,
            )
            while not await is_service_task_running(
                self.docker_client, service["Spec"]["Name"], self.log
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

        except (DockerContainerError, DockerError):
            self.log.exception(
                "Unexpected Error while running container with parameters %s",
                json.dumps(service_parameters, indent=2),
            )
            raise
        except asyncio.CancelledError:
            self.log.warn("Service creation was cancelled")
            raise

    async def do_stop_worker(self, worker: Worker) -> None:
        self.log.debug("Calling to stop worker %s", f"{worker=}")
        service_id = worker.state.get("service_id")
        if service_id:
            self.log.info("Stopping service %s", f"{service_id}")
            try:
                await self.docker_client.services.delete(service_id)
                self.log.info("service %s stopped", f"{service_id=}")

            except DockerContainerError:
                self.log.exception(
                    "Error while stopping service with id %s", f"{service_id=}"
                )
        else:
            self.log.error(
                "Worker %s does not have a service id! That is not expected!",
                f"{worker=}",
            )

    async def _check_service_status(self, worker: Worker) -> bool:
        self.log.debug("checking worker status: %s", f"{worker=}")
        service_id = worker.state.get("service_id")
        if service_id:
            self.log.debug("checking worker %s status", f"{service_id=}")
            try:
                service = await self.docker_client.services.inspect(service_id)
                self.log.debug("checking worker %s associated service", f"{service=}")
                if service:
                    service_name = service["Spec"]["Name"]
                    return await is_service_task_running(
                        self.docker_client, service_name, self.log
                    )

            except DockerContainerError:
                self.log.exception(
                    "Error while checking container with id %s", f"{service_id=}"
                )
        self.log.error(
            "Worker %s does not have a service id! That is not expected!", f"{worker=}"
        )
        return False

    async def do_check_workers(self, workers: List[Worker]) -> List[bool]:
        self.log.debug("--> checking workers statuses: %s", f"{workers=}")
        ok = await asyncio.gather(
            *[self._check_service_status(w) for w in workers], return_exceptions=True
        )
        self.log.debug("<-- worker status returned: %s", f"{ok=}")
        return ok


class UnsafeOsparcBackend(OsparcBackend):  # pylint: disable=too-many-ancestors
    """A version of OsparcBackend that doesn't set permissions.

    This provides no user separations - clusters run with the
    same level of permission as the gateway, which is fine,
    everyone is a scu
    """

    def make_preexec_fn(self, cluster: Cluster) -> Callable[[], None]:
        workdir = cluster.state["workdir"]

        def preexec() -> None:  # pragma: nocover
            os.chdir(workdir)

        return preexec

    def set_file_permissions(self, paths: List[str], username: str) -> None:
        pass
