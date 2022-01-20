import asyncio
from typing import Any, AsyncGenerator, Dict, Final, List, Union

from aiodocker import Docker
from aiodocker.exceptions import DockerContainerError
from dask_gateway_server.backends.base import PublicException
from dask_gateway_server.backends.db_base import Cluster, DBBackendBase, Worker

from .settings import AppSettings
from .utils import (
    DockerSecret,
    create_docker_secrets_from_tls_certs_for_cluster,
    delete_secrets,
    is_service_task_running,
    start_service,
    stop_service,
)

_SCHEDULER_PORT: Final[int] = 8786


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

        # now we need a scheduler
        scheduler_env = self.get_scheduler_env(cluster)
        scheduler_cmd = self.get_scheduler_command(cluster)
        scheduler_service_name = f"cluster_{cluster.id}_scheduler"
        try:
            # NOTE: the healthcheck of itisfoundation/dask-sidecar expects the dashboard to be on port 8787
            dashboard_address_arg_index = scheduler_cmd.index("--dashboard-address")
            scheduler_cmd[dashboard_address_arg_index + 1] = ":8787"
        except ValueError:
            scheduler_cmd.extend(["--dashboard-address", ":8787"])
        try:
            # NOTE: the workers expect to connect with the scheduler on predefined port
            scheduler_port_arg_index = scheduler_cmd.index("--port")
            scheduler_cmd[scheduler_port_arg_index + 1] = f"{_SCHEDULER_PORT}"
            scheduler_host_arg_index = scheduler_cmd.index("--host")
            scheduler_cmd[scheduler_host_arg_index + 1] = scheduler_service_name
        except ValueError:
            scheduler_cmd.extend(["--port", f"{_SCHEDULER_PORT}"])
        self.log.debug("created scheduler command: %s", f"{scheduler_cmd=}")

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
        dask_scheduler_service_id = worker.cluster.state.get("service_id")
        dask_scheduler = await self.docker_client.services.inspect(
            dask_scheduler_service_id
        )
        if not dask_scheduler:
            raise PublicException(
                f"Cluster {worker.cluster.id} associated dask-scheduler is not running!"
            )

        self.log.debug("associated scheduler is %s", f"{dask_scheduler=}")
        dask_scheduler_name = dask_scheduler["Spec"]["Name"]
        worker_env = self.get_worker_env(worker.cluster)
        dask_scheduler_url = f"tls://{dask_scheduler_name}:{_SCHEDULER_PORT}"
        worker_cmd = self.get_worker_command(worker.cluster, worker.name)
        try:
            dask_worker_cmd_index = worker_cmd.index("dask-worker")
            dask_scheduler_url = worker_cmd[dask_worker_cmd_index + 1]
        except ValueError as exc:
            raise PublicException(
                f"Could not find TLS IP to connect to dask-scheduler in {worker_cmd=}"
            ) from exc
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
