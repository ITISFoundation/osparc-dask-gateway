#
#
#

makefile_path 	:= $(abspath $(lastword $(MAKEFILE_LIST)))
makefile_dir 	:= $(patsubst %/,%,$(dir $(makefile_path)))

.DEFAULT_GOAL 	:= help

SHELL 			:= /bin/bash


export VCS_URL:=$(shell git config --get remote.origin.url || echo unversioned)
export VCS_REF:=$(shell git rev-parse --short HEAD || echo unversioned)
export VCS_STATUS:=$(if $(shell git status -s || echo unversioned),'modified/untracked','clean')
export BUILD_DATE:=$(shell date -u +"%Y-%m-%dT%H:%M:%SZ")

define _docker_compose_build
export BUILD_TARGET=$(if $(findstring -devel,$@),development,production);\
docker buildx bake --file docker-compose-build.yml $(if $(target),$(target),);
endef

.PHONY: build build-nc rebuild

rebuild: build-nc # alias
build build-nc: ## Builds production images and tags them as 'local/osparc-dask-gateway:production'. For single target e.g. 'make target=webserver build'
	$(_docker_compose_build)

.PHONY: up
up:
	docker swarm init || true
	docker-compose up

.PHONY: help
help: ## help on rule's targets
	@awk --posix 'BEGIN {FS = ":.*?## "} /^[[:alpha:][:space:]_-]+:.*?## / {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
