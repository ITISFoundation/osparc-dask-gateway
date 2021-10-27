#
#
#

makefile_path 	:= $(abspath $(lastword $(MAKEFILE_LIST)))
makefile_dir 	:= $(patsubst %/,%,$(dir $(makefile_path)))

.DEFAULT_GOAL 	:= help

SHELL 			:= /bin/bash

VENV_DIR 		:= $(makefile_dir)/.venv
VENV_PYTHON 	:= $(VENV_DIR)/bin/python


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

.PHONY: tests

tests:
	# @pytest -vv --failed-first --cov=osparc_dask_gateway $(makefile_dir)/tests/
	@pytest -vv --failed-first --cov=osparc_dask_gateway $(makefile_dir)/tests/
	

.PHONY: help
help: ## help on rule's targets
	@awk --posix 'BEGIN {FS = ":.*?## "} /^[[:alpha:][:space:]_-]+:.*?## / {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
