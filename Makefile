#
#
#

makefile_path 	:= $(abspath $(lastword $(MAKEFILE_LIST)))
makefile_dir 	:= $(patsubst %/,%,$(dir $(makefile_path)))

.DEFAULT_GOAL 	:= help

SHELL 			:= /bin/bash

get_my_ip := $(shell hostname --all-ip-addresses | cut --delimiter=" " --fields=1)
SWARM_HOSTS            = $(shell docker node ls --format="{{.Hostname}}" 2>$(if $(IS_WIN),null,/dev/null))

PHONY: .init-swarm up-swarm down-swarm
.init-swarm:
	# Ensures swarm is initialized
	$(if $(SWARM_HOSTS),,docker swarm init --advertise-addr=$(get_my_ip))

up-prod:  .init-swarm ## run as stack in swarm
	export BUILD_TARGET=production && docker stack deploy --with-registry-auth -c docker-compose-swarm.yml dask-gateway

down: ## remove stack and leave swarm
	docker stack rm dask-gateway

leave: ## Forces to stop all services, networks, etc by the node leaving the swarm
	-docker swarm leave --force

build: ## creates required images
	cd gateway && make build
	cd volume-sync && make build

publish: ## publishes required images
	cd gateway && make publish
	cd volume-sync && make publish

.PHONY: help
help: ## help on rule's targets
	@awk --posix 'BEGIN {FS = ":.*?## "} /^[[:alpha:][:space:]_-]+:.*?## / {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
