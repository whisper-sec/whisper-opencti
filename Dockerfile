FROM python:3.12-alpine

ARG VERSION=dev

LABEL org.opencontainers.image.title="whisper-opencti" \
      org.opencontainers.image.description="OpenCTI connector that enriches indicators with Whisper graph data." \
      org.opencontainers.image.source="https://github.com/whisper-sec/whisper-opencti" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONNECTOR_VERSION=${VERSION} \
    CONNECTOR_TYPE=INTERNAL_ENRICHMENT

WORKDIR /opt/connector

# Runtime libs that pycti transitively pulls in (python-magic needs libmagic;
# cryptography needs libffi). Build-time toolchain is installed as a virtual
# package and removed after pip install to keep the final image small.
# Copy just the requirements first (from src/, per the upstream template
# layout) so the dependency layer caches independently of source changes.
COPY src/requirements.txt ./
# git is needed to install connectors-sdk from its git+https requirement
# (it ships from the OpenCTI-Platform/connectors monorepo, not PyPI). It lives
# in the throwaway .build-deps package so it isn't in the final image.
RUN apk add --no-cache libmagic libffi && \
    apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev git && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del .build-deps

COPY src/ ./src/
COPY entrypoint.sh healthcheck.sh ./
RUN chmod +x entrypoint.sh healthcheck.sh

# Run as a non-root user. UID 10001 matches the OpenCTI platform convention
# and stays clear of common host UIDs (typically 1000-1500).
RUN addgroup -S connector && \
    adduser -S -G connector -u 10001 -h /opt/connector connector && \
    chown -R connector:connector /opt/connector
USER connector

# Liveness check: confirm the python entrypoint is alive. Internal-enrichment
# connectors have no HTTP listener, so process existence is the simplest
# meaningful signal. start-period covers OpenCTI / RabbitMQ connect time.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD ./healthcheck.sh

ENTRYPOINT ["./entrypoint.sh"]