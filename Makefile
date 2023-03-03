#
#
#

.DEFAULT_GOAL 	:= help

SHELL 			:= /bin/bash

MAKE_C := $(MAKE) --no-print-directory --directory

# Operating system
ifeq ($(filter Windows_NT,$(OS)),)
IS_WSL  := $(if $(findstring Microsoft,$(shell uname -a)),WSL,)
IS_WSL2 := $(if $(findstring -microsoft-,$(shell uname -a)),WSL2,)
IS_OSX  := $(filter Darwin,$(shell uname -a))
IS_LINUX:= $(if $(or $(IS_WSL),$(IS_OSX)),,$(filter Linux,$(shell uname -a)))
endif

IS_WIN  := $(strip $(if $(or $(IS_LINUX),$(IS_OSX),$(IS_WSL)),,$(OS)))
$(if $(IS_WIN),$(error Windows is not supported in all recipes. Use WSL2 instead. Follow instructions in README.md),)


makefile_path 	:= $(abspath $(lastword $(MAKEFILE_LIST)))
makefile_dir 	:= $(patsubst %/,%,$(dir $(makefile_path)))

SERVICES_LIST := $(notdir $(subst /Dockerfile,,$(wildcard services/*/Dockerfile)))

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

# osparc-dask-gateway configuration file
export OSPARC_GATEWAY_CONFIG_FILE_HOST = .osparc-dask-gateway-config.py


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

# define _docker_compose_build
# export BUILD_TARGET=$(if $(findstring -devel,$@),development,production);\
# pushd services && docker buildx bake --file docker-compose-build.yml $(if $(target),$(target),) && popd;
# endef

# docker buildx cache location
DOCKER_BUILDX_CACHE_FROM ?= /tmp/.buildx-cache
DOCKER_BUILDX_CACHE_TO ?= /tmp/.buildx-cache
DOCKER_TARGET_PLATFORMS ?= linux/amd64
comma := ,

define _docker_compose_build
$(if $(create_cache),$(info creating cache file in $(DOCKER_BUILDX_CACHE_TO)),)
export BUILD_TARGET=$(if $(findstring -devel,$@),development,production);\
pushd services &&\
$(foreach service, $(SERVICES_LIST),\
	$(if $(push),\
		export $(subst -,_,$(shell echo $(service) | tr a-z A-Z))_VERSION=$(shell cat services/$(service)/VERSION);\
	,) \
)\
docker buildx bake \
	$(if $(findstring -devel,$@),,\
	--set *.platform=$(DOCKER_TARGET_PLATFORMS) \
	$(foreach service, $(SERVICES_LIST),\
		--set $(service).cache-from="type=local,src=$(DOCKER_BUILDX_CACHE_FROM)/$(service)" \
		$(if $(create_cache),--set $(service).cache-to="type=local$(comma)mode=max$(comma)dest=$(DOCKER_BUILDX_CACHE_TO)/$(service)",) \
	)\
	)\
	$(if $(findstring $(comma),$(DOCKER_TARGET_PLATFORMS)),,--set *.output="type=docker$(comma)push=false") \
	$(if $(push),--push,) \
	$(if $(push),--file docker-bake.hcl,) --file docker-compose-build.yml $(if $(target),$(target),) &&\
popd
endef

rebuild: build-nc # alias
build build-nc: .env ## Builds production images and tags them as 'local/{service-name}:production'. For single target e.g. 'make target=osparc-$(SWARM_STACK_NAME) build'
ifeq ($(target),)
	# Building services
	@$(_docker_compose_build)
else
	# Building service $(target)
	@$(_docker_compose_build)
endif


build-devel build-devel-nc: .env ## Builds development images and tags them as 'local/{service-name}:development'. For single target e.g. 'make target=osparc-$(SWARM_STACK_NAME) build-devel'
ifeq ($(target),)
	# Building services
	@$(_docker_compose_build)
else
	# Building service $(target)
	@@$(_docker_compose_build)
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

$(OSPARC_GATEWAY_CONFIG_FILE_HOST): services/osparc-gateway-server/config/default_config.py  ## creates .env file from defaults in .env-devel
	$(if $(wildcard $@), \
	@echo "WARNING #####  $< is newer than $@ ####"; diff -uN $@ $<; false;,\
	@echo "WARNING ##### $@ does not exist, cloning $< as $@ ############"; cp $< $@)


.stack-$(SWARM_STACK_NAME)-development.yml: .env $(docker-compose-configs)
	# Creating config for stack with 'local/{service}:development' to $@
	@export DOCKER_REGISTRY=local \
	export DOCKER_IMAGE_TAG=development; \
	docker compose --env-file .env --file services/docker-compose.yml --file services/docker-compose.local.yml --file services/docker-compose.devel.yml config | sed '/published:/s/"//g' | sed '/name:.*/d' > $@

.stack-$(SWARM_STACK_NAME)-production.yml: .env $(docker-compose-configs)
	# Creating config for stack with 'local/{service}:production' to $@
	@export DOCKER_REGISTRY=local;       \
	export DOCKER_IMAGE_TAG=production; \
	docker compose --env-file .env --file services/docker-compose.yml --file services/docker-compose.local.yml config | sed '/published:/s/"//g' | sed '/name:.*/d'> $@

.stack-$(SWARM_STACK_NAME)-version.yml: .env $(docker-compose-configs)
	# Creating config for stack with '$(DOCKER_REGISTRY)/{service}:${DOCKER_IMAGE_TAG}' to $@
	@docker compose --env-file .env --file services/docker-compose.yml --file services/docker-compose.local.yml config | sed '/published:/s/"//g' | sed '/name:.*/d' > $@

.stack-$(SWARM_STACK_NAME)-ops.yml: .env $(docker-compose-configs)
	# Creating config for ops stack to $@
	@docker compose --env-file .env --file services/docker-compose-ops.yml config | sed '/published:/s/"//g' | sed '/name:.*/d' > $@


.PHONY: up-devel up-prod up-version up-latest

.deploy-ops: .stack-$(SWARM_STACK_NAME)-ops.yml
	# Deploy stack 'ops'
ifndef ops_disabled
	@docker stack deploy --with-registry-auth --compose-file $< $(SWARM_STACK_NAME)-ops
else
	@echo "Explicitly disabled with ops_disabled flag in CLI"
endif

define _show_endpoints
# The following endpoints are available
set -o allexport; \
source $(CURDIR)/.env; \
set +o allexport; \
separator=------------------------------------------------------------------------------------;\
separator=$${separator}$${separator}$${separator};\
rows="%-22s | %40s | %12s | %12s\n";\
TableWidth=100;\
printf "%22s | %40s | %12s | %12s\n" Name Endpoint User Password;\
printf "%.$${TableWidth}s\n" "$$separator";\
$(if $(shell docker stack ps ${SWARM_STACK_NAME}-ops 2>/dev/null), \
printf "$$rows" Portainer 'http://$(get_my_ip):9000' admin adminadmin;,)\
printf "$$rows" Dask-Gateway 'http://$(get_my_ip):8000' whatever $(filter-out %.password =,$(shell cat $(OSPARC_GATEWAY_CONFIG_FILE_HOST) | grep c.Authenticator.password));
endef

show-endpoints:
	@$(_show_endpoints)


up-devel: .stack-$(SWARM_STACK_NAME)-development.yml .init-swarm config  ## Deploys local development stack and ops stack (pass 'make ops_disabled=1 up-...' to disable)
	# Deploy stack $(SWARM_STACK_NAME) [back-end]
	@docker stack deploy --with-registry-auth -c $< $(SWARM_STACK_NAME)
	@$(MAKE) .deploy-ops
	@$(_show_endpoints)

up-prod: .stack-$(SWARM_STACK_NAME)-production.yml .init-swarm config ## Deploys local production stack and ops stack (pass 'make ops_disabled=1 up-...' to disable)
ifeq ($(target),)
	# Deploy stack $(SWARM_STACK_NAME)
	@docker stack deploy --with-registry-auth -c $< $(SWARM_STACK_NAME)
	@$(MAKE) .deploy-ops
else
	# deploys ONLY $(target) service
	@docker compose --file $< up --detach $(target)
endif
	@$(_show_endpoints)

up up-version: .stack-$(SWARM_STACK_NAME)-version.yml .init-swarm config ## Deploys versioned stack '$(DOCKER_REGISTRY)/{service}:$(DOCKER_IMAGE_TAG)' and ops stack (pass 'make ops_disabled=1 up-...' to disable)
	# Deploy stack $(SWARM_STACK_NAME)
	@docker stack deploy --with-registry-auth -c $< $(SWARM_STACK_NAME)
	@$(_show_endpoints)

up-latest:
	@export DOCKER_IMAGE_TAG=latest; \
	$(MAKE) up-version

.PHONY: down leave
down: ## Stops and removes stack
	# Removing stacks in reverse order to creation
	-@docker stack rm $(SWARM_STACK_NAME)
	-@docker stack rm $(SWARM_STACK_NAME)-ops
	# Removing generated docker compose configurations, i.e. .stack-*
	-@rm $(wildcard .stack-*)
	-@rm $(wildcard $(OSPARC_GATEWAY_CONFIG_FILE_HOST))

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
	@docker compose --file services/docker-compose-deploy.yml pull


.PHONY: push-version push-latest

push-latest: tag-latest
	@export DOCKER_IMAGE_TAG=latest; \
	$(MAKE) push-version

# below BUILD_TARGET gets overwritten but is required when merging yaml files
push-version: tag-version
	# pushing '${DOCKER_REGISTRY}/{service}:${DOCKER_IMAGE_TAG}'
	@export BUILD_TARGET=undefined; \
	docker compose --file services/docker-compose-build.yml --file services/docker-compose-deploy.yml push

## ENVIRONMENT -------------------------------

.PHONY: devenv

.venv:
	python3 -m venv $@
	$@/bin/pip3 --quiet install --upgrade \
		pip~=21.3 \
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

.PHONY: info info-images info-swarm  info-tools
info: ## displays setup information
	# setup info:
	@echo ' Detected OS          : $(IS_LINUX)$(IS_OSX)$(IS_WSL)$(IS_WSL2)$(IS_WIN)'
	@echo ' SWARM_STACK_NAME     : ${SWARM_STACK_NAME}'
	@echo ' DOCKER_REGISTRY      : $(DOCKER_REGISTRY)'
	@echo ' DOCKER_IMAGE_TAG     : ${DOCKER_IMAGE_TAG}'
	@echo ' BUILD_DATE           : ${BUILD_DATE}'
	@echo ' VCS_* '
	@echo '  - ULR                : ${VCS_URL}'
	@echo '  - REF                : ${VCS_REF}'
	# dev tools version
	@echo ' make          : $(shell make --version 2>&1 | head -n 1)'
	@echo ' jq            : $(shell jq --version)'
	@echo ' awk           : $(shell awk -W version 2>&1 | head -n 1)'
	@echo ' python        : $(shell python3 --version)'
	@echo ' node          : $(shell node --version 2> /dev/null || echo ERROR nodejs missing)'
	@echo ' docker        : $(shell docker --version)'
	@echo ' docker buildx : $(shell docker buildx version)'
	@echo ' docker-compose: $(shell docker compose --version)'


define show-meta
	$(foreach iid,$(shell docker images "*/$(1):*" -q | sort | uniq),\
		docker image inspect $(iid) | jq '.[0] | .RepoTags, .Config.Labels, .Architecture';)
endef

info-images:  ## lists tags and labels of built images. To display one: 'make target=webserver info-images'
ifeq ($(target),)
	@$(foreach service,$(SERVICES_LIST),\
		echo "## $(service) images:";\
			docker images */$(service):*;\
			$(call show-meta,$(service))\
		)
else
	## $(target) images:
	@$(call show-meta,$(target))
endif

info-swarm: ## displays info about stacks and networks
ifneq ($(SWARM_HOSTS), )
	# Stacks in swarm
	@docker stack ls
	# Containers (tasks) running in '$(SWARM_STACK_NAME)' stack
	-@docker stack ps $(SWARM_STACK_NAME)
	# Services in '$(SWARM_STACK_NAME)' stack
	-@docker stack services $(SWARM_STACK_NAME)
	# Networks
	@docker network ls
endif


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


## Initial config

.PHONY: config
config: $(OSPARC_GATEWAY_CONFIG_FILE_HOST)  ## create configuration file
