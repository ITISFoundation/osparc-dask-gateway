#
#
#

.DEFAULT_GOAL 	:= help

SHELL 			:= /bin/bash

MAKE_C := $(MAKE) --no-print-directory --directory

makefile_path 	:= $(abspath $(lastword $(MAKEFILE_LIST)))
makefile_dir 	:= $(patsubst %/,%,$(dir $(makefile_path)))



# version control
export VCS_URL          := $(shell git config --get remote.origin.url)
export VCS_REF          := $(shell git rev-parse --short HEAD)
export VCS_STATUS_CLIENT:= $(if $(shell git status -s),'modified/untracked','clean')
export BUILD_DATE       := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")

# swarm stacks
export SWARM_STACK_NAME ?= dask-gateway

# version tags
export DOCKER_IMAGE_TAG ?= latest
export DOCKER_REGISTRY  ?= itisfoundation

get_my_ip := $(shell hostname --all-ip-addresses | cut --delimiter=" " --fields=1)


.PHONY: help
help: ## help on rule's targets
	@awk --posix 'BEGIN {FS = ":.*?## "} /^[[:alpha:][:space:]_-]+:.*?## / {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)


## DOCKER BUILD -------------------------------
#
# - all builds are inmediatly tagged as 'local/{service}:${BUILD_TARGET}' where BUILD_TARGET='development', 'production', 'cache'
# - only production and cache images are released (i.e. tagged pushed into registry)
#
SWARM_HOSTS = $(shell docker node ls --format="{{.Hostname}}" 2>$(if $(IS_WIN),NUL,/dev/null))

.PHONY: build build-nc rebuild build-devel build-devel-nc

define _docker_compose_build
export BUILD_TARGET=$(if $(findstring -devel,$@),development,production);\
pushd services && docker buildx bake --file docker-compose-build.yml $(if $(target),$(target),) && popd;
endef

rebuild: build-nc # alias
build build-nc: .env ## Builds production images and tags them as 'local/{service-name}:production'. For single target e.g. 'make target=osparc-$(SWARM_STACK_NAME) build'
ifeq ($(target),)
	# Building services
	$(_docker_compose_build)
else
	# Building service $(target)
	$(_docker_compose_build)
endif


build-devel build-devel-nc: .env ## Builds development images and tags them as 'local/{service-name}:development'. For single target e.g. 'make target=osparc-$(SWARM_STACK_NAME) build-devel'
ifeq ($(target),)
	# Building services
	$(_docker_compose_build)
else
	# Building service $(target)
	@$(_docker_compose_build)
endif

.PHONY: shell
shell:
	docker run -it local/$(target):production /bin/sh

## DOCKER SWARM -------------------------------
#
# - All resolved configuration are named as .stack-${name}-*.yml to distinguish from docker-compose files which can be parametrized
#
SWARM_HOSTS            = $(shell docker node ls --format="{{.Hostname}}" 2>$(if $(IS_WIN),null,/dev/null))
docker-compose-configs = $(wildcard services/docker-compose*.yml)

.stack-$(SWARM_STACK_NAME)-development.yml: .env $(docker-compose-configs)
	# Creating config for stack with 'local/{service}:development' to $@
	@export DOCKER_REGISTRY=local \
	export DOCKER_IMAGE_TAG=development; \
	docker-compose --env-file .env --file services/docker-compose.yml --file services/docker-compose.local.yml --file services/docker-compose.devel.yml --log-level=ERROR config > $@

.stack-$(SWARM_STACK_NAME)-production.yml: .env $(docker-compose-configs)
	# Creating config for stack with 'local/{service}:production' to $@
	@export DOCKER_REGISTRY=local;       \
	export DOCKER_IMAGE_TAG=production; \
	docker-compose --env-file .env --file services/docker-compose.yml --file services/docker-compose.local.yml --log-level=ERROR config > $@

.stack-$(SWARM_STACK_NAME)-version.yml: .env $(docker-compose-configs)
	# Creating config for stack with '$(DOCKER_REGISTRY)/{service}:${DOCKER_IMAGE_TAG}' to $@
	@docker-compose --env-file .env --file services/docker-compose.yml --file services/docker-compose.local.yml --log-level=ERROR config > $@


.PHONY: up-devel up-prod up-version up-latest

up-devel: .stack-$(SWARM_STACK_NAME)-development.yml .init-swarm ## Deploys local development stack
	# Deploy stack $(SWARM_STACK_NAME) [back-end]
	@docker stack deploy --with-registry-auth -c $< $(SWARM_STACK_NAME)

up-prod: .stack-$(SWARM_STACK_NAME)-production.yml .init-swarm ## Deploys local production stack
ifeq ($(target),)
	# Deploy stack $(SWARM_STACK_NAME)
	@docker stack deploy --with-registry-auth -c $< $(SWARM_STACK_NAME)
else
	# deploys ONLY $(target) service
	@docker-compose --file $< up --detach $(target)
endif

up-version: .stack-$(SWARM_STACK_NAME)-version.yml .init-swarm ## Deploys versioned stack '$(DOCKER_REGISTRY)/{service}:$(DOCKER_IMAGE_TAG)'
	# Deploy stack $(SWARM_STACK_NAME)
	@docker stack deploy --with-registry-auth -c $< $(SWARM_STACK_NAME)

up-latest:
	@export DOCKER_IMAGE_TAG=latest; \
	$(MAKE) up-version

.PHONY: down leave
down: ## Stops and removes stack
	# Removing stacks in reverse order to creation
	-@$(foreach stack,\
		$(shell docker stack ls --format={{.Name}} | tac),\
		docker stack rm $(stack);)
	# Removing generated docker compose configurations, i.e. .stack-*
	-@rm $(wildcard .stack-*)

leave: ## Forces to stop all services, networks, etc by the node leaving the swarm
	-docker swarm leave -f

PHONY: .init-swarm
.init-swarm:
	# Ensures swarm is initialized
	$(if $(SWARM_HOSTS),,docker swarm init --advertise-addr=$(get_my_ip))


## DOCKER TAGS  -------------------------------

.PHONY: tag-local tag-version tag-latest

tag-local: ## Tags version '${DOCKER_REGISTRY}/{service}:${DOCKER_IMAGE_TAG}' images as 'local/{service}:production'
	# Tagging all '${DOCKER_REGISTRY}/{service}:${DOCKER_IMAGE_TAG}' as 'local/{service}:production'
	@$(foreach service, $(SERVICES_LIST)\
		,docker tag ${DOCKER_REGISTRY}/$(service):${DOCKER_IMAGE_TAG} local/$(service):production; \
	)

tag-version: ## Tags 'local/{service}:production' images as versioned '${DOCKER_REGISTRY}/{service}:${DOCKER_IMAGE_TAG}'
	# Tagging all 'local/{service}:production' as '${DOCKER_REGISTRY}/{service}:${DOCKER_IMAGE_TAG}'
	@$(foreach service, $(SERVICES_LIST)\
		,docker tag local/$(service):production ${DOCKER_REGISTRY}/$(service):${DOCKER_IMAGE_TAG}; \
	)

tag-latest: ## Tags last locally built production images as '${DOCKER_REGISTRY}/{service}:latest'
	@export DOCKER_IMAGE_TAG=latest; \
	$(MAKE) tag-version



## DOCKER PULL/PUSH  -------------------------------
#
# TODO: cannot push modified/untracked
# TODO: cannot push disceted
#
.PHONY: pull-version

pull-version: .env ## pulls images from DOCKER_REGISTRY tagged as DOCKER_IMAGE_TAG
	# Pulling images '${DOCKER_REGISTRY}/{service}:${DOCKER_IMAGE_TAG}'
	@docker-compose --file services/docker-compose-deploy.yml pull


.PHONY: push-version push-latest

push-latest: tag-latest
	@export DOCKER_IMAGE_TAG=latest; \
	$(MAKE) push-version

# below BUILD_TARGET gets overwritten but is required when merging yaml files
push-version: tag-version
	# pushing '${DOCKER_REGISTRY}/{service}:${DOCKER_IMAGE_TAG}'
	@export BUILD_TARGET=undefined; \
	docker-compose --file services/docker-compose-build.yml --file services/docker-compose-deploy.yml push

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

.env: .env-devel ## creates .env file from defaults in .env-devel
	$(if $(wildcard $@), \
	@echo "WARNING #####  $< is newer than $@ ####"; diff -uN $@ $<; false;,\
	@echo "WARNING ##### $@ does not exist, cloning $< as $@ ############"; cp $< $@)


.vscode/settings.json: .vscode-template/settings.json
	$(info WARNING: #####  $< is newer than $@ ####)
	@diff -uN $@ $<
	@false

## CLEAN -------------------------------

.PHONY: clean clean-images clean-venv clean-all clean-more

_git_clean_args := -dx --force --exclude=.vscode --exclude=TODO.md --exclude=.venv --exclude=.python-version --exclude=*keep*
_running_containers = $(shell docker ps -aq)

.check-clean:
	@git clean -n $(_git_clean_args)
	@echo -n "Are you sure? [y/N] " && read ans && [ $${ans:-N} = y ]
	@echo -n "$(shell whoami), are you REALLY sure? [y/N] " && read ans && [ $${ans:-N} = y ]

clean-venv: devenv ## Purges .venv into original configuration
	# Cleaning your venv
	.venv/bin/pip-sync --quiet $(CURDIR)/requirements/devenv.txt
	@pip list

clean-hooks: ## Uninstalls git pre-commit hooks
	@-pre-commit uninstall 2> /dev/null || rm .git/hooks/pre-commit

clean: .check-clean ## cleans all unversioned files in project and temp files create by this makefile
	# Cleaning unversioned
	@git clean $(_git_clean_args)

clean-more: ## cleans containers and unused volumes
	# stops and deletes running containers
	@$(if $(_running_containers), docker rm --force $(_running_containers),)
	# pruning unused volumes
	docker volume prune --force

clean-all: clean clean-more clean-images clean-hooks # Deep clean including .venv and produced images
	-rm -rf .venv


.PHONY: reset
reset: ## restart docker daemon (LINUX ONLY)
	sudo systemctl restart docker
