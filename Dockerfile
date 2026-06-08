FROM python:3.11-slim

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
    CONNECTOR_VERSION=${VERSION}

WORKDIR /opt/connector

# pycti -> python-magic needs libmagic at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
