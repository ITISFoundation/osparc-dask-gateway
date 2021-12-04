#
#
#

makefile_path 	:= $(abspath $(lastword $(MAKEFILE_LIST)))
makefile_dir 	:= $(patsubst %/,%,$(dir $(makefile_path)))

.DEFAULT_GOAL 	:= help

SHELL 			:= /bin/bash

MAKE_C := $(MAKE) --no-print-directory --directory

get_my_ip := $(shell hostname --all-ip-addresses | cut --delimiter=" " --fields=1)
SWARM_HOSTS            = $(shell docker node ls --format="{{.Hostname}}" 2>$(if $(IS_WIN),null,/dev/null))

PHONY: .init-swarm up-swarm down-swarm
.init-swarm:
	# Ensures swarm is initialized
	$(if $(SWARM_HOSTS),,docker swarm init --advertise-addr=$(get_my_ip))

up-prod:  .init-swarm ## run as stack in swarm
	@export BUILD_TARGET=production && \
	docker stack deploy \
	--with-registry-auth \
	--compose-file docker-compose-swarm.yml \
	dask-gateway

up-devel: .init-swarm ## run as stack in swarm in devel mode
	# Deploy stack
	@export BUILD_TARGET=development && \
	docker stack deploy \
	--with-registry-auth \
	--compose-file docker-compose-swarm.yml \
	--compose-file docker-compose.devel.yml \
	dask-gateway


down: ## remove stack and leave swarm
	docker stack rm dask-gateway

leave: ## Forces to stop all services, networks, etc by the node leaving the swarm
	-docker swarm leave --force

.PHONY: build build-nc rebuild
build build-devel rebuild: ## creates required images
	$(MAKE_C) gateway $@
	$(MAKE_C) volume-sync $@

.PHONY: publish
publish: ## publishes required images
	$(MAKE_C) gateway $@
	$(MAKE_C) volume-sync $@

.PHONY: help
help: ## help on rule's targets
	@awk --posix 'BEGIN {FS = ":.*?## "} /^[[:alpha:][:space:]_-]+:.*?## / {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

## ENVIRONMENT -------------------------------

.PHONY: devenv

.venv:
	python3 -m venv $@
	$@/bin/pip3 --quiet install --upgrade \
		pip \
		wheel \
		setuptools

devenv: .venv ## create a python virtual environment with dev tools (e.g. linters, etc)
	$</bin/pip3 --quiet install -r requirements/devenv.txt
	# Installing pre-commit hooks in current .git repo
	@$</bin/pre-commit install
	@echo "To activate the venv, execute 'source .venv/bin/activate'"

.vscode/settings.json: .vscode-template/settings.json
	$(info WARNING: #####  $< is newer than $@ ####)
	@diff -uN $@ $<
	@false
