# syntax=docker/dockerfile:1
ARG PYTHON_VERSION="3.8.12"
FROM --platform=${TARGETPLATFORM} python:${PYTHON_VERSION}-buster as base
ARG TARGETPLATFORM
ARG BUILDPLATFORM
RUN echo "I am running on $BUILDPLATFORM, building for $TARGETPLATFORM" > /log \
  && stat -c "%a %n" /var/cache/apt/ && stat -c "%a %n" /var/lib/apt/



LABEL maintainer=mguidon,sanderegg

# libffi-dev is needed for ARM architectures
RUN  --mount=type=cache,id=basecache,target=/var/cache/apt,mode=0755,sharing=locked \
  --mount=type=cache,id=baseapt,target=/var/lib/apt,mode=0755,sharing=locked \
  set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
  iputils-ping \
  curl \
  gosu \
  procps \
  libffi-dev \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/* \
  # verify that the binary works
  && gosu nobody true


# simcore-user uid=8004(scu) gid=8004(scu) groups=8004(scu)
ENV SC_USER_ID=8004 \
  SC_USER_NAME=scu \
  SC_BUILD_TARGET=base \
  SC_BOOT_MODE=default

RUN adduser \
  --uid ${SC_USER_ID} \
  --disabled-password \
  --gecos "" \
  --shell /bin/sh \
  --home /home/${SC_USER_NAME} \
  ${SC_USER_NAME}


ENV LANG=C.UTF-8 \
  PYTHONDONTWRITEBYTECODE=1 \
  VIRTUAL_ENV=/home/scu/.venv

ENV PATH="${VIRTUAL_ENV}/bin:$PATH"


EXPOSE 8080
EXPOSE 8786
EXPOSE 8787
EXPOSE 8000

# -------------------------- Build stage -------------------
# Installs build/package management tools and third party dependencies
#
# + /build             WORKDIR
#
FROM base as build

ENV SC_BUILD_TARGET=build

RUN rm -f /etc/apt/apt.conf.d/docker-clean
RUN  --mount=type=cache,id=build-cache,target=/var/cache/apt,mode=0755,sharing=locked \
  --mount=type=cache,id=build-apt,target=/var/lib/apt,mode=0755,sharing=locked \
  apt-get update \
  && apt-get install -y --no-install-recommends \
  build-essential \
  git


# NOTE: python virtualenv is used here such that installed packages may be moved to production image easily by copying the venv
RUN python -m venv "${VIRTUAL_ENV}"
RUN --mount=type=cache,mode=0777,target=/root/.cache/pip \
  pip install --upgrade \
  pip~=21.3.1  \
  wheel \
  setuptools

WORKDIR /build

# copy osparc-gateway-server and dependencies
COPY --chown=scu:scu services/osparc-gateway-server/requirements/_base.txt .

# install base 3rd party dependencies (NOTE: this speeds up devel mode)
ENV MAKEFLAGS="-j 12"
RUN --mount=type=cache,mode=0777,target=/root/.cache/pip \
  pip install \
  -r _base.txt

# --------------------------Prod-depends-only stage -------------------
# This stage is for production only dependencies that get partially wiped out afterwards (final docker image concerns)
#
#  + /build
#    + services/osparc-gateway-server [scu:scu] WORKDIR
#
FROM build as prod-only-deps

ENV SC_BUILD_TARGET=prod-only-deps

COPY --chown=scu:scu services/osparc-gateway-server/ /build/services/osparc-gateway-server

WORKDIR /build/services/osparc-gateway-server

RUN --mount=type=cache,mode=0777,target=/root/.cache/pip \
  pip install -r requirements/prod.txt

# --------------------------Production stage -------------------
# Final cleanup up to reduce image size and startup setup
# Runs as scu (non-root user)
#
#  + /home/scu     $HOME = WORKDIR
#    + services/osparc-gateway-server [scu:scu]
#
FROM base as production

ENV SC_BUILD_TARGET=production \
  SC_BOOT_MODE=production
ENV PYTHONOPTIMIZE=TRUE

WORKDIR /home/scu

# bring installed package without build tools
COPY --from=prod-only-deps --chown=scu:scu ${VIRTUAL_ENV} ${VIRTUAL_ENV}
# copy docker entrypoint and boot scripts
COPY --chown=scu:scu services/osparc-gateway-server/docker services/osparc-gateway-server/docker
COPY --chown=scu:scu services/osparc-gateway-server/config services/osparc-gateway-server/config


# WARNING: This image is used for dask-scheduler and dask-worker.
# In order to have the same healty entrypoint port
# make sure dask worker is started as ``dask-worker --dashboard-address 8787``.
# Otherwise the worker will take random ports to serve the /health entrypoint.
# HEALTHCHECK \
#   --interval=60s \
#   --timeout=60s \
#   --start-period=10s \
#   --retries=3 \
#   CMD ["curl", "-Lf", "http://127.0.0.1:8787/health"]
RUN chmod +x services/osparc-gateway-server/docker/*.sh
ENTRYPOINT [ "/bin/sh", "services/osparc-gateway-server/docker/entrypoint.sh" ]
CMD ["/bin/sh", "services/osparc-gateway-server/docker/boot.sh"]


# --------------------------Development stage -------------------
# Source code accessible in host but runs in container
# Runs as scu with same gid/uid as host
# Placed at the end to speed-up the build if images targeting production
#
#  + /devel         WORKDIR
#    + services  (mounted volume)
#
FROM build as development

ENV SC_BUILD_TARGET=development

WORKDIR /devel
RUN chown -R scu:scu "${VIRTUAL_ENV}"

# NOTE: devel mode does NOT have HEALTHCHECK

ENTRYPOINT [ "/bin/sh", "services/osparc-gateway-server/docker/entrypoint.sh" ]
CMD ["/bin/sh", "services/osparc-gateway-server/docker/boot.sh"]
