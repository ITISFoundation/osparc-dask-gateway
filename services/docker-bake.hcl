variable "DOCKER_REGISTRY" {
  default = "itisfoundation"
}

variable "DOCKER_IMAGE_TAG" {
  default = "latest"
}

target "osparc-gateway-server" {
    tags = ["${DOCKER_REGISTRY}/osparc-gateway-server:latest","${DOCKER_REGISTRY}/osparc-gateway-server:${DOCKER_IMAGE_TAG}"]
    output = ["type=registry"]
}

target "volume-sync" {
    tags = ["${DOCKER_REGISTRY}/volume-sync:latest","${DOCKER_REGISTRY}/volume-sync:${DOCKER_IMAGE_TAG}"]
    output = ["type=registry"]
}
