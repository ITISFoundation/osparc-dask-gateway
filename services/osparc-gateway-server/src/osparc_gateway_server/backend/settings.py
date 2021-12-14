from typing import Optional

from pydantic import BaseSettings, Field


class AppSettings(BaseSettings):
    GATEWAY_WORKERS_NETWORK: str = Field(
        ...,
        description="The docker network where the gateway workers shall be able to access the gateway",
    )
    GATEWAY_SERVER_NAME: str = Field(
        ...,
        description="The hostname of the gateway server in the GATEWAY_WORKERS_NETWORK network",
    )

    COMPUTATIONAL_SIDECAR_VOLUME_NAME: str = Field(
        ..., description="Named volume for the computational sidecars"
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
