import asyncio
import errno
import functools
import grp
import os
import pwd
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from aiodocker import Docker
from aiodocker.exceptions import DockerContainerError, DockerError
from aiodocker.volumes import DockerVolume
from dask_gateway_server.backends.base import ClusterConfig
from dask_gateway_server.backends.local import LocalBackend
from dask_gateway_server.traitlets import Type
from traitlets import Integer, List, Unicode

__all__ = ("OsparcClusterConfig", "OsparcBackend", "UnsafeOsparcBackend")


class OsparcClusterConfig(ClusterConfig):
    """Dask cluster configuration options when running as osparc backend"""

    pass


class OsparcBackend(LocalBackend):
    """A cluster backend that launches osparc workers.

    Requires super-user permissions in order to run processes for the
    requesting username.
    """

    cluster_config_class = Type(
        "osparc_dask_gateway.backend.osparc.OsparcClusterConfig",
        klass="dask_gateway_server.backends.base.ClusterConfig",
        help="The cluster config class to use",
        config=True,
    )
    
    default_host = "0.0.0.0"
    containers = {}

    async def do_start_worker(self, worker):
        cmd = self.get_worker_command(worker.cluster, worker.name)
        env = self.get_worker_env(worker.cluster)

        scheduler_url = urlsplit(worker.cluster.scheduler_address)
        port = scheduler_url.netloc.split(":")[1]
        netloc = "dask-gateway_dask-gateway-server-osparc" + ":" + port
        scheduler_address = urlunsplit(scheduler_url._replace(netloc=netloc))

        db_address = f"{self.default_host}:8787"
        workdir = worker.cluster.state.get("workdir")

        nthreads, memory_limit = self.worker_nthreads_memory_limit_args(worker.cluster)

        env.update(
            {
                "DASK_SCHEDULER_ADDRESS": scheduler_address,
                "DASK_DASHBOARD_ADDRESS": db_address,
                "DASK_NTHREADS": nthreads,
                "DASK_MEMORY_LIMIT": memory_limit,
                "DASK_WORKER_NAME": f"{worker.name}",
                "GATEWAY_WORK_FOLDER": f"{workdir}",
                "SIDECAR_COMP_SERVICES_SHARED_FOLDER": f"{workdir}",
                "SIDECAR_HOST_HOSTNAME_PATH": f"{workdir}",
                "SIDECAR_COMP_SERVICES_SHARED_VOLUME_NAME": "comp_gateway",
                "SC_BOOT_MODE": "debug",
            }
        )

        docker_image = "local/dask-sidecar:production"
        workdir = worker.cluster.state.get("workdir")

        container_config = {}
        try:
            async with Docker() as docker_client:
                for folder in [
                    f"{workdir}/input",
                    f"{workdir}/output",
                    f"{workdir}/log",
                ]:
                    p = Path(folder)
                    p.mkdir(parents=True, exist_ok=True)

                volume_attributes = await DockerVolume(
                    docker_client, "dask-gateway_gateway_data"
                ).show()
                vol_mount_point = volume_attributes["Mountpoint"]

                mounts = [
                    # docker socket needed to use the docker api
                    {
                        "Source": "/var/run/docker.sock",
                        "Target": "/var/run/docker.sock",
                        "Type": "bind",
                        "ReadOnly": True,
                    },
                    # {
                    #    # "Source": f"{vol_mount_point}/{worker.cluster.name}/input",
                    #     "Source": f"{workdir}/input",
                    #     "Target": "/input",
                    #     "Type": "bind",
                    #     "ReadOnly": False,
                    # },
                    # {
                    #     #"Source": f"{vol_mount_point}/{worker.cluster.name}/output",
                    #     "Source": f"{workdir}/output",
                    #     "Target": "/output",
                    #     "Type": "bind",
                    #     "ReadOnly": False,
                    # },
                    # {
                    #     # "Source": f"{vol_mount_point}/{worker.cluster.name}/log",
                    #     "Source": f"{workdir}/log",
                    #     "Target": "/log",
                    #     "Type": "bind",
                    #     "ReadOnly": False,
                    # },
                    {
                        # "Source": f"{vol_mount_point}/{worker.cluster.name}",
                        "Source": f"{workdir}",
                        "Target": f"{workdir}",
                        "Type": "bind",
                        "ReadOnly": False,
                    },
                ]

                container_config = {
                    "Env": env,
                    "Image": docker_image,
                    "Init": True,
                    "Mounts": mounts,
                }

                network_name = "_dask_net"
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

                service = await docker_client.services.create(**service_parameters)
                self.log.info("Service %s started", service_name)

                if "ID" not in service:
                    # error while starting service
                    self.log.error("OOPS service not created")

                # get the full info from docker
                service = await docker_client.services.inspect(service["ID"])
                service_name = service["Spec"]["Name"]
                self.log.info("Waiting for service %s to start", service_name)
                while True:
                    tasks = await docker_client.tasks.list(
                        filters={"service": service_name}
                    )
                    if tasks and len(tasks) == 1:
                        task = tasks[0]
                        task_state = task["Status"]["State"]
                        self.log.info("%s %s", service["ID"], task_state)
                        if task_state in ("failed", "rejected"):
                            self.log.error(
                                "Error while waiting for service with %s",
                                task["Status"],
                            )
                        if task_state in ("running", "complete"):
                            break
                    await asyncio.sleep(1)

                self.log.info("Service %s is running", worker.name)
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
            self.log.warn("Container run was cancelled")
            raise

    async def _stop_service(self, worker):
        service_id = worker.state.get("service_id")
        if service_id is not None:
            self.log.info("Stopping service %s", service_id)
            try:
                async with Docker() as docker_client:
                    await docker_client.services.delete(service_id)

            except DockerContainerError:
                self.log.exception(
                    "Error while stopping service with id %s", service_id
                )

    async def do_stop_worker(self, worker):
        await self._stop_service(worker)

    async def _check_service_status(self, worker):
        service_id = worker.state.get("service_id")
        if service_id:
            try:
                async with Docker() as docker_client:
                    service = await docker_client.services.inspect(service_id)
                    if service:
                        service_name = service["Spec"]["Name"]
                        tasks = await docker_client.tasks.list(
                            filters={"service": service_name}
                        )
                        if tasks and len(tasks) == 1:
                            service_state = tasks[0]["Status"]["State"]
                            self.log.info(
                                "State of %s  is %s", service_name, service_state
                            )
                            return service_state == "running"
            except DockerContainerError:
                self.log.exception(
                    "Error while checking container with id %s", service_id
                )

        return False

    async def do_check_workers(self, workers):
        ok = [False] * len(workers)
        for i, w in enumerate(workers):
            ok[i] = await self._check_service_status(w)

        return ok


class UnsafeOsparcBackend(OsparcBackend):
    """A version of OsparcBackend that doesn't set permissions.

    FOR TESTING ONLY! This provides no user separations - clusters run with the
    same level of permission as the gateway.
    """

    def make_preexec_fn(self, cluster):
        workdir = cluster.state["workdir"]

        def preexec():  # pragma: nocover
            os.chdir(workdir)

        return preexec

    def set_file_permissions(self, paths, username):
        pass
