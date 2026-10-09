# syntax=docker/dockerfile:1
# First supported platform: Linux x86_64. No credentials or customer data in images.
FROM node:22-bookworm-slim AS codex
ARG CODEX_VERSION=0.159.2
RUN npm install --prefix /opt/codex-build "@openai/codex@${CODEX_VERSION}" \
    && cp -a /opt/codex-build/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl /codex

FROM python:3.12-slim-bookworm AS runtime
ARG TARGETARCH
RUN test "$TARGETARCH" = amd64 \
    && apt-get update && apt-get install -y --no-install-recommends \
       bubblewrap ca-certificates curl poppler-utils libreoffice-writer \
       fonts-noto-cjk fonts-dejavu-core tini xvfb xauth x11-utils \
       libglib2.0-0 libgl1 libdbus-1-3 libx11-6 libxrender1 libxext6 \
       libxcb1 libxcb-shm0 libxcb-render0 libxcb-xfixes0 libxcb-xinerama0 \
       libxcb-cursor0 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libfreetype6 \
       libsm6 libice6 libnss3 libasound2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=codex /codex /usr/local/lib/codex
RUN ln -s /usr/local/lib/codex/bin/codex /usr/local/bin/codex \
    && codex --version \
    && codex exec --help > /tmp/codex-help \
    && for flag in --ignore-user-config --ignore-rules --ephemeral --output-schema; do \
         grep -q -- "$flag" /tmp/codex-help || exit 1; done \
    && rm /tmp/codex-help
ENV SKILL_DIR=/workspace/.agents/skills/valvue3-offline-diagnostic \
    HOME=/home/diagnostic CODEX_HOME=/home/diagnostic/.codex \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANG=C.UTF-8 \
    DISPLAY=:99
COPY skill/valvue3-offline-diagnostic/ /workspace/.agents/skills/valvue3-offline-diagnostic/
RUN python -c 'import sys; sys.path.insert(0,"/workspace/.agents/skills/valvue3-offline-diagnostic/scripts"); from offline_engine.distribution import verify_package; print(verify_package())' \
    && pip install --no-cache-dir -r "$SKILL_DIR/requirements.txt" \
    && useradd --create-home --uid 1000 diagnostic \
    && mkdir -p /home/diagnostic/.codex /workspace/离线诊断/projects \
    && chown -R diagnostic:diagnostic /home/diagnostic /workspace/离线诊断 \
    && chmod -R a-w "$SKILL_DIR"
COPY docker/entrypoint.sh /usr/local/bin/valvue-entrypoint
COPY docker/doctor.py /usr/local/lib/valvue/doctor.py
COPY docker/ocr_smoke.py /usr/local/lib/valvue/ocr_smoke.py
COPY docker/isolation_smoke.py /usr/local/lib/valvue/isolation_smoke.py
RUN chmod 755 /usr/local/bin/valvue-entrypoint
WORKDIR /workspace
USER diagnostic
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/valvue-entrypoint"]
CMD ["shell"]

# Use an official installer you are entitled to install. It is never committed.
# BuildKit reads it from context without preserving the .deb in an image layer.
FROM runtime AS with-wps
USER root
ARG WPS_DEB_SHA256
RUN --mount=type=bind,source=docker/installers,target=/installers,readonly \
    test ${#WPS_DEB_SHA256} -eq 64 \
    && echo "$WPS_DEB_SHA256  /installers/wps-office.deb" | sha256sum --check --strict - \
    && test "$(dpkg-deb -f /installers/wps-office.deb Architecture)" = amd64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends /installers/wps-office.deb \
    && command -v wps \
    && rm -rf /var/lib/apt/lists/*
USER diagnostic

FROM python:3.12-slim-bookworm AS ocr
ARG TARGETARCH
RUN test "$TARGETARCH" = amd64 \
    && apt-get update && apt-get install -y --no-install-recommends \
       ca-certificates libglib2.0-0 libgl1 libgomp1 fonts-dejavu-core tini \
    && rm -rf /var/lib/apt/lists/*
COPY docker/requirements-ocr.txt /tmp/requirements-ocr.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ocr.txt && pip check \
    && python -c 'from paddleocr import PaddleOCR, PPStructureV3' \
    && useradd --create-home --uid 1000 diagnostic \
    && mkdir -p /opt/models && chown diagnostic:diagnostic /opt/models
ENV HOME=/opt/models PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 OMP_NUM_THREADS=4
COPY docker/ocr_server.py /app/ocr_server.py
COPY docker/ocr_smoke.py /app/ocr_smoke.py
USER diagnostic
WORKDIR /app
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/ocr_server.py"]
