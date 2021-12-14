import asyncio
import json
import os
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List
from urllib.parse import SplitResult, urlsplit, urlunsplit

from aiodocker import Docker
from aiodocker.exceptions import DockerContainerError, DockerError
from dask_gateway_server.backends.base import ClusterConfig
from dask_gateway_server.backends.db_base import Cluster, Worker
from dask_gateway_server.backends.local import LocalBackend
from dask_gateway_server.traitlets import Type

from .settings import AppSettings
from .utils import (
    DockerSecret,
    create_or_update_secret,
    create_service_config,
    delete_secrets,
    get_network_id,
    is_service_task_running,
    start_service,
    stop_service,
)

__all__ = ("OsparcClusterConfig", "OsparcBackend", "UnsafeOsparcBackend")


class OsparcClusterConfig(ClusterConfig):
    """Dask cluster configuration options when running as osparc backend"""


async def _create_docker_secrets_from_tls_certs(
    docker_client: Docker, tls_cert_path: str, tls_key_path: str, cluster: Cluster
) -> List[DockerSecret]:
    return [
        await create_or_update_secret(
            docker_client,
            f"{cluster.id}_{Path(tls_cert_path).name}",
            Path(tls_cert_path),
            cluster,
            worker_env=[
                "DASK_DISTRIBUTED__COMM__TLS__CA_FILE",
                "DASK_DISTRIBUTED__COMM__TLS__WORKER__CERT",
            ],
            scheduler_env=[
                "DASK_DISTRIBUTED__COMM__TLS__CA_FILE",
                "DASK_DISTRIBUTED__COMM__TLS__SCHEDULER__CERT",
            ],
        ),
        await create_or_update_secret(
            docker_client,
            f"{cluster.id}_{Path(tls_key_path).name}",
            Path(tls_key_path),
            cluster,
            worker_env=[
                "DASK_DISTRIBUTED__COMM__TLS__WORKER__KEY",
            ],
            scheduler_env=[
                "DASK_DISTRIBUTED__COMM__TLS__SCHEDULER__KEY",
            ],
        ),
    ]


def _replace_netloc_in_url(original_url: str, settings: AppSettings) -> str:
    splitted_url: SplitResult = urlsplit(original_url)
    port = splitted_url.netloc.split(":")[1]
    new_netloc = f"{settings.GATEWAY_SERVER_NAME}:{port}"
    return urlunsplit(splitted_url._replace(netloc=new_netloc))


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
    cluster_secrets: List[DockerSecret] = []

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

    async def do_start_cluster(self, cluster) -> AsyncGenerator[Dict[str, Any], None]:
        last_result = None
        async for result in super().do_start_cluster(cluster):
            last_result = result
            yield result
        self.cluster_secrets.extend(
            await _create_docker_secrets_from_tls_certs(
                self.docker_client, *self.get_tls_paths(cluster), cluster
            )
        )
        self.log.debug(
            "created secrets for TLS certification: %s", f"{self.cluster_secrets=}"
        )
        assert last_result is not None  # nosec

        # now we need a scheduler
        gateway_api_url = _replace_netloc_in_url(self.api_url, self.settings)
        async for dask_scheduler_start_result in start_service(
            self.docker_client,
            self.settings,
            self.log,
            f"cluster_{cluster.id}_scheduler",
            {"DASK_START_AS_SCHEDULER": "1"},
            self.cluster_secrets,
            gateway_api_url,
        ):
            last_result.update(dask_scheduler_start_result)
            yield last_result

    async def do_stop_cluster(self, cluster):
        dask_scheduler_service_id = cluster.state.get("service_id")
        await stop_service(self.docker_client, dask_scheduler_service_id, self.log)
        await delete_secrets(self.docker_client, cluster)
        return await super().do_stop_cluster(cluster)

    async def do_start_worker(
        self, worker: Worker
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self.log.debug("received call to start worker as %s", f"{worker=}")

        scheduler_url = _replace_netloc_in_url(
            worker.cluster.scheduler_address, self.settings
        )
        gateway_api_url = _replace_netloc_in_url(self.api_url, self.settings)

        workdir = worker.cluster.state.get("workdir")
        self.log.debug("workdir set as %s", f"{workdir=}")

        async for dask_sidecar_start_result in start_service(
            self.docker_client,
            self.settings,
            self.log,
            f"cluster_{worker.cluster.id}_sidecar_{worker.name}",
            {"DASK_SCHEDULER_ADDRESS": "tls://dask-scheduler:8786"},
            self.cluster_secrets,
            gateway_api_url,
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
