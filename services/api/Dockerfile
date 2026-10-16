# syntax=docker/dockerfile:1.7
# AutoForge API, kernel workers and simulation job worker (one image, different commands).
#
# Base: Ubuntu 24.04 LTS so the image ships CalculiX 2.21, the solver version the published validation benchmarks
# (docs/validation/simulation-benchmarks.md) were produced with. Pulled from a mirror of the Docker Official
# Images (Google pull-through cache of Docker Hub; same content, fewer anonymous rate limits).
#
# Builds behind a TLS-inspecting proxy: pass HTTPS_PROXY as a build arg and the proxy CA as a BuildKit secret
#   docker build --secret id=extra_ca,src=proxy-ca.crt ...
# The CA is used only while building and is not left in the image.
ARG BASE_IMAGE=mirror.gcr.io/library/ubuntu:24.04

FROM ${BASE_IMAGE} AS runtime
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
# OCCT (OCP wheels) and Gmsh load a few X/GL runtime libraries even when headless.
RUN --mount=type=secret,id=extra_ca,required=false \
    apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && if [ -s /run/secrets/extra_ca ]; then cp /run/secrets/extra_ca /usr/local/share/ca-certificates/build-extra-ca.crt && update-ca-certificates >/dev/null; fi \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv \
      libgl1 libglu1-mesa libglx-mesa0 libxrender1 libxcursor1 libxft2 libxinerama1 libxi6 libxext6 libxfixes3 libsm6 libice6 \
      libfontconfig1 libgomp1 calculix-ccx tini curl \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv /opt/venv \
 && groupadd --system --gid 10001 autoforge \
 && useradd --system --uid 10001 --gid autoforge --home-dir /app --shell /usr/sbin/nologin autoforge
WORKDIR /app
COPY requirements.txt ./
RUN --mount=type=secret,id=extra_ca,required=false \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt pip install -r requirements.txt \
 && rm -f /usr/local/share/ca-certificates/build-extra-ca.crt && update-ca-certificates --fresh >/dev/null
COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY autoforge ./autoforge
COPY scripts ./scripts
RUN mkdir -p /data/objects /tmp/autoforge && chown -R autoforge:autoforge /data /tmp/autoforge \
 && ccx -v | tail -1 && python -c "import OCP, gmsh; print('OCP and Gmsh import OK')"
ENV AF_STORAGE_LOCAL_DIR=/data/objects TMPDIR=/tmp/autoforge
USER autoforge
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "autoforge.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# Test image: runtime + dev dependencies + tests. CI runs the suite in it against real Postgres/Redis containers.
FROM runtime AS test
USER root
COPY requirements-dev.txt ./
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then cp /run/secrets/extra_ca /usr/local/share/ca-certificates/build-extra-ca.crt && update-ca-certificates >/dev/null; fi \
 && PIP_CERT=/etc/ssl/certs/ca-certificates.crt pip install -r requirements-dev.txt \
 && rm -f /usr/local/share/ca-certificates/build-extra-ca.crt && update-ca-certificates --fresh >/dev/null
COPY tests ./tests
USER autoforge
CMD ["python", "-m", "pytest", "-o", "addopts=", "-q", "-p", "no:cacheprovider"]
