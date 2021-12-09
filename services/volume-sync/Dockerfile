ARG PYTHON_VERSION="3.7.12"
FROM python:${PYTHON_VERSION}-slim-buster as base

LABEL maintainer=mguidon

ENV DEBIAN_FRONTEND=noninteractive

RUN set -eux \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
  gosu \
  unison \
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


# -------------------------- Build stage -------------------
# Installs build/package management tools and third party dependencies
#
# + /build             WORKDIR
#
FROM base as build

ENV SC_BUILD_TARGET=build

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
  build-essential \
  git \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# NOTE: python virtualenv is used here such that installed packages may be moved to production image easily by copying the venv
RUN python -m venv "${VIRTUAL_ENV}"
RUN pip --no-cache-dir install --upgrade \
  pip~=21.2.3  \
  wheel \
  setuptools

WORKDIR /build

# deps
COPY services/volume-sync/requirements.txt .
RUN pip --no-cache-dir install \
  -r requirements.txt

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
COPY --from=build --chown=scu:scu ${VIRTUAL_ENV} ${VIRTUAL_ENV}
# COPY Files
COPY --chown=scu:scu  services/volume-sync/docker/* /opt/
COPY --chown=scu:scu  services/volume-sync/scripts/* /opt/

ENV VOLUME_MOUNTPOINT=/volumes/sync
RUN mkdir -p ${VOLUME_MOUNTPOINT}



ENTRYPOINT [ "/bin/sh", "/opt/entrypoint.sh" ]
CMD ["/bin/sh", "/opt/boot.sh"]
