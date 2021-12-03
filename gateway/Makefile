# osparc-dask-gateway general makefile
#
# NOTES:
# 	- GNU make version 4.2 recommended
# 	- Use 'make -n *' to dry-run during debugging
# 	- In windows, only WSL2 is supported
#
# by mguidon, sanderegg
#

makefile_path 	        := $(abspath $(lastword $(MAKEFILE_LIST)))
makefile_dir 	        := $(patsubst %/,%,$(dir $(makefile_path)))

.DEFAULT_GOAL 	        := help

SHELL 			        := /bin/bash

VENV_DIR 		        := $(makefile_dir)/.venv
VENV_PYTHON 	        := $(VENV_DIR)/bin/python

export SERVICE_NAME    	:= osparc-dask-gateway

export VCS_URL			:= $(shell git config --get remote.origin.url || echo unversioned)
export VCS_REF			:= $(shell git rev-parse --short HEAD || echo unversioned)
export VCS_STATUS		:= $(if $(shell git status -s || echo unversioned),'modified/untracked','clean')
export BUILD_DATE		:= $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")

.PHONY: devenv .check-venv-active

.check-venv-active: ## check that the (correct) venv is activated
	# checking that the virtual environment (${1}) was activated
	@python3 -c "import sys, pathlib; assert pathlib.Path('${1}').resolve()==pathlib.Path(sys.prefix).resolve()" || (echo "--> To activate venv: source ${1}/bin/activate" && exit 1)

devenv: $(VENV_DIR) ## builds development environment
	# Installing python tools in $<
	@$</bin/pip --no-cache install \
		bump2version \
		pip-tools
	# Installing repo packages
	@$</bin/pip install -r $(makefile_dir)/requirements/dev.txt
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


define _docker_compose_build
export BUILD_TARGET=$(if $(findstring -devel,$@),development,production);\
docker buildx bake --file docker-compose-build.yml $(if $(target),$(target),);
endef

.PHONY: build build-nc rebuild

rebuild: build-nc # alias
build build-nc: $(VENV_DIR) ## Builds production images and tags them as 'local/osparc-dask-gateway:production'. For single target e.g. 'make target=webserver build'
ifeq ($(target),)
	# Building services
	$(_docker_compose_build)
else
	# Building service $(target)
	$(_docker_compose_build)
endif

.PHONY: tests

tests:
	# @pytest -vv --failed-first --cov=osparc_dask_gateway $(makefile_dir)/tests/
	@pytest -vv --failed-first --cov=osparc_dask_gateway $(makefile_dir)/tests/
	
publish:
	docker tag local/$(SERVICE_NAME):production mguidon/$(SERVICE_NAME):production
	docker push mguidon/$(SERVICE_NAME):production

.PHONY: help
help: ## help on rule's targets
	@awk --posix 'BEGIN {FS = ":.*?## "} /^[[:alpha:][:space:]_-]+:.*?## / {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
