import asyncio
from typing import Any, AsyncGenerator, Dict, Final, List, Union

from aiodocker import Docker
from aiodocker.exceptions import DockerContainerError
from dask_gateway_server.backends.base import PublicException
from dask_gateway_server.backends.db_base import Cluster, DBBackendBase, Worker

from .settings import AppSettings
from .utils import (
    OSPARC_SCHEDULER_PORT,
    DockerSecret,
    create_docker_secrets_from_tls_certs_for_cluster,
    delete_secrets,
    get_osparc_scheduler_cmd_modifications,
    is_service_task_running,
    modify_cmd_argument,
    start_service,
    stop_service,
)


class OsparcBackend(DBBackendBase):
    """A cluster backend that launches osparc workers.

    Workers are spawned as services in a docker swarm
    """

    default_host = "0.0.0.0"
    # worker_start_timeout = 120

    settings: AppSettings
    docker_client: Docker
    cluster_secrets: List[DockerSecret] = []

    async def do_setup(self) -> None:
        await super().do_setup()
        self.settings = AppSettings()  # type: ignore
        self.docker_client = Docker()
        self.log.info(
            "osparc-gateway-server application settings:\n%s",
            self.settings.json(indent=2),
        )

    async def do_cleanup(self) -> None:
        await super().do_cleanup()
        await self.docker_client.close()

    async def do_start_cluster(
        self, cluster: Cluster
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self.log.debug(f"starting cluster {cluster=}")
        self.cluster_secrets.extend(
            await create_docker_secrets_from_tls_certs_for_cluster(
                self.docker_client, self, cluster
            )
        )
        self.log.debug(
            "created secrets for TLS certification: %s", f"{self.cluster_secrets=}"
        )

        # now we need a scheduler (get these auto-generated entries from dask-gateway base class)
        scheduler_env = self.get_scheduler_env(cluster)
        scheduler_cmd = self.get_scheduler_command(cluster)
        # we need a few modifications for running in docker swarm
        scheduler_service_name = f"cluster_{cluster.id}_scheduler"
        modifications = get_osparc_scheduler_cmd_modifications(scheduler_service_name)
        for key, value in modifications.items():
            scheduler_cmd = modify_cmd_argument(scheduler_cmd, key, value)

        async for dask_scheduler_start_result in start_service(
            self.docker_client,
            self.settings,
            self.log,
            service_name=scheduler_service_name,
            base_env=scheduler_env,
            cluster_secrets=[
                c for c in self.cluster_secrets if c.cluster.name == cluster.name
            ],
            cmd=scheduler_cmd,
            labels={"cluster_id": f"{cluster.id}"},
            gateway_api_url=self.api_url,
        ):
            yield dask_scheduler_start_result

    async def do_stop_cluster(self, cluster: Cluster):
        dask_scheduler_service_id = cluster.state.get("service_id")
        await stop_service(self.docker_client, dask_scheduler_service_id, self.log)
        await delete_secrets(self.docker_client, cluster)

    async def do_check_clusters(self, clusters: List[Cluster]):
        self.log.debug("--> checking clusters statuses: %s", f"{clusters=}")
        ok = await asyncio.gather(
            *[self._check_service_status(c) for c in clusters], return_exceptions=True
        )
        self.log.debug("<-- clusters status returned: %s", f"{ok=}")
        return ok

    async def do_start_worker(
        self, worker: Worker
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self.log.debug("received call to start worker as %s", f"{worker=}")
        worker_env = self.get_worker_env(worker.cluster)
        dask_scheduler_url = f"tls://cluster_{worker.cluster.id}_scheduler:{OSPARC_SCHEDULER_PORT}"  #  worker.cluster.scheduler_address
        # NOTE: the name must be set so that the scheduler knows which worker to wait for
        worker_env.update(
            {
                "DASK_SCHEDULER_URL": dask_scheduler_url,
                "DASK_WORKER_NAME": worker.name,
            }
        )
        async for dask_sidecar_start_result in start_service(
            self.docker_client,
            self.settings,
            self.log,
            f"cluster_{worker.cluster.id}_sidecar_{worker.id}",
            worker_env,
            [c for c in self.cluster_secrets if c.cluster.name == worker.cluster.name],
            cmd=None,
            labels={"cluster_id": f"{worker.cluster.id}", "worker_id": f"{worker.id}"},
            gateway_api_url=self.api_url,
        ):
            yield dask_sidecar_start_result

    async def do_stop_worker(self, worker: Worker) -> None:
        self.log.debug("Calling to stop worker %s", f"{worker=}")
        if service_id := worker.state.get("service_id"):
            await stop_service(self.docker_client, service_id, self.log)
        else:
            self.log.error(
                "Worker %s does not have a service id! That is not expected!",
                f"{worker=}",
            )

    async def _check_service_status(
        self, cluster_service: Union[Worker, Cluster]
    ) -> bool:
        self.log.debug("checking worker status: %s", f"{cluster_service=}")
        if service_id := cluster_service.state.get("service_id"):
            self.log.debug("checking service %s status", f"{service_id=}")
            try:
                service = await self.docker_client.services.inspect(service_id)
                self.log.debug("checking service %s associated", f"{service=}")
                if service:
                    service_name = service["Spec"]["Name"]
                    return await is_service_task_running(
                        self.docker_client, service_name, self.log
                    )

            except DockerContainerError:
                self.log.exception(
                    "Error while checking container with id %s", f"{service_id=}"
                )
        self.log.warning(
            "Worker %s does not have a service id! That is not expected!",
            f"{cluster_service=}",
        )
        return False

    async def do_check_workers(self, workers: List[Worker]) -> List[bool]:
        self.log.debug("--> checking workers statuses: %s", f"{workers=}")
        ok = await asyncio.gather(
            *[self._check_service_status(w) for w in workers], return_exceptions=True
        )
        self.log.debug("<-- worker status returned: %s", f"{ok=}")
        return ok
