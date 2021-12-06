#
#
#

makefile_path 			:= $(abspath $(lastword $(MAKEFILE_LIST)))
makefile_dir 			:= $(patsubst %/,%,$(dir $(makefile_path)))

.DEFAULT_GOAL 			:= help

SHELL 					:= /bin/bash
VENV_DIR                := $(makefile_dir)/.venv

export SERVICE_NAME    	:= osparc-volume-sync

export VCS_URL			:= $(shell git config --get remote.origin.url || echo unversioned)
export VCS_REF			:= $(shell git rev-parse --short HEAD || echo unversioned)
export VCS_STATUS		:= $(if $(shell git status -s || echo unversioned),'modified/untracked','clean')
export BUILD_DATE		:= $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")

define _docker_compose_build
export BUILD_TARGET=$(if $(findstring -devel,$@),development,production);\
docker buildx bake --file docker-compose-build.yml $(if $(target),$(target),);
endef


.PHONY: devenv
devenv: $(VENV_DIR) ## builds development environment
    # Installing python tools in $<
	@$</bin/pip --no-cache install \
			bump2version \
			pip-tools
	# Installing repo packages
	@$</bin/pip install -r $(makefile_dir)/requirements.txt
	# Installed packages in $<
	@$</bin/pip list

$(VENV_DIR):
	# creating virtual environment
	@python3 -m venv $@
	# updating package managers
	@$@/bin/pip --no-cache install --upgrade \
			pip \
			setuptools \
			wheel


.PHONY: build build-nc rebuild

rebuild: build-nc # alias
build build-nc: ## Builds production images and tags them as 'local/osparc-gateway-server:production'
	$(_docker_compose_build)

.PHONY: up down
up:
	docker swarm init || true
	docker stack deploy -c docker-compose.yml sync-stack

down:
	docker stack rm sync-stack
	docker swarm leave -f

test:
	export container_id=$(shell docker ps -q) && echo $${container_id} && docker exec -it --user 8004:8004 $${container_id} /bin/bash -c "date >> /volumes/sync/now.dat"

publish:
	docker tag local/$(SERVICE_NAME):production mguidon/$(SERVICE_NAME):production
	docker push mguidon/$(SERVICE_NAME):production

.PHONY: help
help: ## help on rule's targets
	@awk --posix 'BEGIN {FS = ":.*?## "} /^[[:alpha:][:space:]_-]+:.*?## / {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
