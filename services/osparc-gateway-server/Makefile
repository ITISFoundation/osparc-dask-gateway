# osparc-dask-gateway general makefile
#
# NOTES:
# 	- GNU make version 4.2 recommended
# 	- Use 'make -n *' to dry-run during debugging
# 	- In windows, only WSL2 is supported
#
# by mguidon, sanderegg
#

include ../../scripts/common.Makefile
include ../../scripts/common-service.Makefile

.PHONY: requirements reqs
requirements reqs: ## (or reqs) compiles pip requirements (.in -> .txt)
	@$(MAKE_C) requirements reqs
