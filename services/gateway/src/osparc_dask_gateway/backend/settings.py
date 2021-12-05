from typing import Optional

from pydantic import BaseSettings, Field


class AppSettings(BaseSettings):
    GATEWAY_VOLUME_NAME: str = Field(
        ..., description="Named volume as defined on the host machine"
    )
    GATEWAY_WORK_FOLDER: str = Field(
        ...,
        description="Mounted folder in the gateway application corresponding to GATEWAY_VOLUME_NAME",
    )
    GATEWAY_WORKERS_NETWORK: str = Field(
        ...,
        description="The docker network where the gateway workers shall be able to access the gateway",
    )
    GATEWAY_SERVER_NAME: str = Field(
        ...,
        description="The hostname of the gateway server in the GATEWAY_WORKERS_NETWORK network",
    )

    COMPUTATIONAL_SIDECAR_IMAGE: str = Field(
        ..., description="The computational sidecar image in use"
    )
    COMPUTATIONAL_SIDECAR_LOG_LEVEL: Optional[str] = Field(
        "WARNING",
        description="The computational sidecar log level",
        env=[
            "COMPUTATIONAL_SIDECAR_LOG_LEVEL",
            "LOG_LEVEL",
            "LOGLEVEL",
            "SIDECAR_LOG_LEVEL",
            "SIDECAR_LOGLEVEL",
        ],
    )
