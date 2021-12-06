ARG PYTHON_VERSION="3.8.10"
FROM python:${PYTHON_VERSION}-slim-buster as base

LABEL maintainer="guidon@itis.swiss"

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -yq --no-install-recommends \
    gosu \
    unison \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    # verify that the binary works
    && gosu nobody true

# deps
COPY requirements.txt .
RUN pip install -r requirements.txt

# simcore-user uid=8004(scu) gid=8004(scu) groups=8004(scu)
ENV SC_USER_ID=8004 \
    SC_USER_NAME=scu

RUN adduser \
    --uid ${SC_USER_ID} \
    --disabled-password \
    --gecos "" \
    --shell /bin/sh \
    --home /home/${SC_USER_NAME} \
    ${SC_USER_NAME}

ENV VOLUME_MOUNTPOINT=/volumes/sync

RUN mkdir -p ${VOLUME_MOUNTPOINT}

# COPY Files
COPY docker/* /opt/
COPY scripts/* /opt/

ENTRYPOINT [ "/bin/sh", "/opt/entrypoint.sh" ]
CMD ["/bin/sh", "/opt/boot.sh"]
