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
	--compose-file services/docker-compose-swarm.yml \
	dask-gateway

up-devel: .init-swarm ## run as stack in swarm in devel mode
	# Deploy stack
	@export BUILD_TARGET=development && \
	docker stack deploy \
	--with-registry-auth \
	--compose-file services/docker-compose-swarm.yml \
	--compose-file services/docker-compose.devel.yml \
	dask-gateway


down: ## remove stack and leave swarm
	docker stack rm dask-gateway

leave: ## Forces to stop all services, networks, etc by the node leaving the swarm
	-docker swarm leave --force

.PHONY: build build-nc rebuild
build build-devel rebuild: ## creates required images
	$(MAKE_C) services/gateway $@
	$(MAKE_C) services/volume-sync $@

.PHONY: publish
publish: ## publishes required images
	$(MAKE_C) services/gateway $@
	$(MAKE_C) services/volume-sync $@

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