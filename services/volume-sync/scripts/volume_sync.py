#!/usr/bin/env python3

import logging as logger
import os
import socket
import struct
import subprocess
import time
from pathlib import Path
from typing import List, Union

import click

logger.basicConfig(level=logger.DEBUG)
logger.debug("Debugging log level is enabled")


def try_kill_process(process: subprocess.Popen):
    try:
        process.kill()
    except Exception as e:
        logger.info("Could not kill process %s: " % (str(process), str(e)))


def get_container_ip() -> str:
    return socket.gethostbyname_ex(socket.gethostname() + ".")[2][0]


class VolumeSyncher:
    """
    Wrapper around command line tool unison https://www.cis.upenn.edu/~bcpierce/unison/.
    Synchronizes folder contents among nodes. It assumes that the folder to synchronize
    have the exact same absoulute path on all nodes.

    """

    def __init__(
        self,
        sync_folder: Path,  # folder to be synchronized with peers
        wait_period: int,  # initial waiting period before synchronization starts
        sync_interval: int,  # interval between sync attempts
        sync_timeout: int,  # timeout for sync process
        sync_type: str,  # sync type
    ):
        self.sync_folder = sync_folder
        self.wait_period = wait_period
        self.sync_interval = sync_interval
        self.sync_timeout = sync_timeout if sync_timeout > 0 else None
        self.sync_type = sync_type

        self.sync_server_port = 2222
        self.server_cmd = ["unison", "-socket", f"{self.sync_server_port}"]
        self.client_cmd = [
            "unison",
            "-auto",
            "-batch",
            "-fastcheck",
            "-group",
            "-owner",
            "-prefer=newer",
            "-silent",
            "-times",
            "-confirmbigdel=false",
            "-confirmmerge=false",
            "-ignore=Name {logs,log,input,output}",  # TODO: Should be a click argument
        ]

        self.shutdown = False
        self.server_process = Union[None, subprocess.Popen]

        self.hostname_group = os.getenv("HOSTNAME", "sync")

    def get_group_ips(self) -> List[str]:
        # Get the ips of all replicas without that of this container
        sync_group_ips = socket.gethostbyname_ex(self.hostname_group + ".")[2]
        logger.debug(("IPs of group are %s") % sync_group_ips)
        if sync_group_ips is None:
            sync_group_ips = []

        return sync_group_ips

    def get_sorted_group_ips(self) -> List[str]:
        group_ips = self.get_group_ips()
        # Sort ips from 0.0.0.0 to 255.255.255.255 by each byte value
        return sorted(
            group_ips, key=lambda ip: struct.unpack("!L", socket.inet_aton(ip))[0]
        )

    def check_sync_server(self):
        # Shutdown if sync server has stopped
        if self.server_process and self.server_process.poll() != None:
            logger.warn("Sync server is not running anymore. Shutting down!")
            self.shutdown = True

    def start_sync_server(self):
        args = self.server_cmd
        self.server_process = subprocess.Popen(args)
        logger.debug("Sync server started with args: %s" % args)

    def sync(self):
        all_sync_ips = self.get_sorted_group_ips()
        logger.debug("Group ips are %s" % all_sync_ips)
        container_ip = get_container_ip()
        logger.debug("Container ip is %s" % container_ip)
        if container_ip not in all_sync_ips:
            logger.error(
                "IP of Container %s not part of group %s. Do you have all connected the hosts via network?"
                % (container_ip, all_sync_ips)
            )
        container_ip_pos = all_sync_ips.index(container_ip)
        # This container must be part of the network
        if container_ip_pos < 0:
            logger.warn(
                "Container ip %s not in group ip %s" % (container_ip, all_sync_ips)
            )
        # Create list of sync targets
        sync_ips = []
        if self.sync_type == "FIRST":
            sync_ips = [all_sync_ips[0]]
        elif self.sync_type == "NEXT":
            next_index = (container_ip_pos + 1) % len(all_sync_ips)
            sync_ips = [all_sync_ips[next_index]]
        elif self.sync_type == "ALL":
            sync_ips = all_sync_ips

        # Container should not sync to itself
        if container_ip in sync_ips:
            sync_ips.remove(container_ip)

        # If there is no sync partner then cancel
        if not sync_ips:
            logger.info("No ips to sync.")
            return

        for sync_ip in sync_ips:
            # Cancel when shutdown
            if self.shutdown:
                return

            sync_target = "socket://%s:%s/%s" % (
                sync_ip,
                self.sync_server_port,
                self.sync_folder,
            )
            args = self.client_cmd + [self.sync_folder] + [sync_target]
            logger.info(
                "Running sync (Timeout: %s) with args: %s " % (self.sync_timeout, args)
            )
            sync_process = subprocess.Popen(args)
            returncode = sync_process.wait(timeout=self.sync_timeout)
            if returncode == None:
                logger.warn("Could not finish sync in timeout %s" % self.sync_timeout)
                self.try_kill_process(sync_process)

        logger.debug("Sync process stopped with exit code %s" % returncode)

    def run(self):
        last_sync = time.time()
        while not self.shutdown:
            self.check_sync_server()
            if time.time() - last_sync > self.sync_interval:
                logger.debug("Now syncing")
                last_sync = time.time()
                self.sync()
            next_sync = self.sync_interval - (time.time() - last_sync)
            if next_sync > 0:
                logger.debug("Next sync in %ss" % next_sync)
                time.sleep(next_sync)

    def kill_server_process(self):
        try_kill_process(self.server_process)

    def go(self):
        # start server
        self.start_sync_server()

        # wait a moment
        time.sleep(self.wait_period)

        # run until stop
        self.run()

        # Shutdown server
        self.kill_server_process()


@click.command()
@click.option("--sync-folder", type=str, help="Folder path to synchronize")
@click.option(
    "--wait-period",
    type=int,
    default=10,
    help="Initial wait time after server start until first sync starts",
)
@click.option(
    "--sync-interval",
    type=int,
    default=10,
    help="Interval between synchronization attempts",
)
@click.option(
    "--sync-timeout",
    type=int,
    default=0,
    help="Timeout for synchronization process (0=None)",
)
@click.option("--sync-type", type=click.Choice(["NEXT", "FIRST", "ALL"]), default="ALL")
def vol_sync(
    sync_folder: str,
    wait_period: int,
    sync_interval: int,
    sync_timeout: int,
    sync_type: click.Choice,
):
    volume_syncher = VolumeSyncher(
        sync_folder=sync_folder,
        wait_period=wait_period,
        sync_interval=sync_interval,
        sync_timeout=sync_timeout,
        sync_type=sync_type,
    )

    volume_syncher.go()


if __name__ == "__main__":
    vol_sync()
