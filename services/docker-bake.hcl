variable "DOCKER_REGISTRY" {
  default = "itisfoundation"
}

variable "OSPARC_GATEWAY_SERVER_VERSION" {
  default = "latest"
}

variable "VOLUME_SYNC_VERSION" {
  default = "latest"
}

target "osparc-gateway-server" {
    tags = ["${DOCKER_REGISTRY}/osparc-gateway-server:latest","${DOCKER_REGISTRY}/osparc-gateway-server:${OSPARC_GATEWAY_SERVER_VERSION}"]
    output = ["type=registry"]
}

target "volume-sync" {
    tags = ["${DOCKER_REGISTRY}/volume-sync:latest","${DOCKER_REGISTRY}/volume-sync:${VOLUME_SYNC_VERSION}"]
    output = ["type=registry"]
}
