import asyncio
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Union
from urllib.parse import SplitResult, urlsplit, urlunsplit

from aiodocker import Docker
from aiodocker.exceptions import DockerContainerError
from dask_gateway_server.backends.db_base import Cluster, DBBackendBase, Worker
from traitlets import Unicode

from .settings import AppSettings
from .utils import (
    DockerSecret,
    create_or_update_secret,
    delete_secrets,
    is_service_task_running,
    start_service,
    stop_service,
)

__all__ = ("OsparcBackend", "UnsafeOsparcBackend")


async def _create_docker_secrets_from_tls_certs(
    docker_client: Docker, backend: DBBackendBase, cluster: Cluster
) -> List[DockerSecret]:
    tls_cert_path, tls_key_path = backend.get_tls_paths(cluster)
    return [
        await create_or_update_secret(
            docker_client,
            f"{tls_cert_path}",
            cluster,
            secret_data=cluster.tls_cert.decode(),
        ),
        await create_or_update_secret(
            docker_client,
            f"{tls_key_path}",
            cluster,
            secret_data=cluster.tls_key.decode(),
        ),
    ]


def _replace_netloc_in_url(original_url: str, settings: AppSettings) -> str:
    splitted_url: SplitResult = urlsplit(original_url)
    port = splitted_url.netloc.split(":")[1]
    new_netloc = f"{settings.GATEWAY_SERVER_NAME}:{port}"
    return urlunsplit(splitted_url._replace(netloc=new_netloc))


def _cluster_base_env(
    backend: DBBackendBase, cluster: Cluster, settings: AppSettings
) -> Dict[str, str]:
    gateway_api_url = _replace_netloc_in_url(backend.api_url, settings)
    return {
        "DASK_GATEWAY_API_URL": gateway_api_url,
        "DASK_GATEWAY_API_TOKEN": cluster.token,
        "DASK_GATEWAY_CLUSTER_NAME": f"{cluster.name}",
        "DASK_DISTRIBUTED__COMM__REQUIRE_ENCRYPTION": "True",
    }


class OsparcBackend(DBBackendBase):
    """A cluster backend that launches osparc workers.

    Workers are spawned as services in a docker swarm
    """

    default_host = "0.0.0.0"
    # worker_start_timeout = 120

    settings: AppSettings
    docker_client: Docker
    cluster_secrets: List[DockerSecret] = []

    clusters_directory = Unicode(
        "/tmp/clusters_directory",
        help="Path to use for keeping the clusters directories",
        config=True,
    )

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

    async def do_start_cluster(
        self, cluster: Cluster
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self.cluster_secrets.extend(
            await _create_docker_secrets_from_tls_certs(
                self.docker_client, self, cluster
            )
        )
        self.log.debug(
            "created secrets for TLS certification: %s", f"{self.cluster_secrets=}"
        )

        # now we need a scheduler
        scheduler_env = self.get_scheduler_env(cluster)
        scheduler_cmd = self.get_scheduler_command(cluster)
        scheduler_env.update(
            {
                "DASK_START_AS_SCHEDULER": "1",
                "DASK_SCHEDULER_OPTIONS": " ".join(scheduler_cmd),
            }
        )
        async for dask_scheduler_start_result in start_service(
            self.docker_client,
            self.settings,
            self.log,
            f"cluster_{cluster.id}_scheduler",
            scheduler_env,
            self.cluster_secrets,
            cmd=None,
        ):
            yield dask_scheduler_start_result

    async def do_stop_cluster(self, cluster: Cluster):
        dask_scheduler_service_id = cluster.state.get("service_id")
        await stop_service(self.docker_client, dask_scheduler_service_id, self.log)
        await delete_secrets(self.docker_client, cluster)
        return await super().do_stop_cluster(cluster)

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

        workdir = worker.cluster.state.get("workdir")
        self.log.debug("workdir set as %s", f"{workdir=}")
        base_env = _cluster_base_env(self, worker.cluster, self.settings)
        base_env.update({"DASK_SCHEDULER_ADDRESS": "tls://dask-scheduler:8786"})
        async for dask_sidecar_start_result in start_service(
            self.docker_client,
            self.settings,
            self.log,
            f"cluster_{worker.cluster.id}_sidecar_{worker.name}",
            base_env,
            self.cluster_secrets,
            cmd=None,
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
        service_id = cluster_service.state.get("service_id")
        if service_id:
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
        self.log.error(
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
