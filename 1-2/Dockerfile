FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS install
WORKDIR /build
COPY dist/requirements.txt dist/*.whl ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --require-hashes -r requirements.txt \
    && /opt/venv/bin/pip install --no-cache-dir --no-deps ./*.whl \
    && /opt/venv/bin/pip uninstall -y pip

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS runtime
RUN groupadd --gid 10001 manager \
    && useradd --uid 10001 --gid manager --no-create-home --shell /usr/sbin/nologin manager \
    && mkdir /data \
    && chown manager:manager /data \
    && chmod 0700 /data
COPY --from=install /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    FM_ROOT=/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /data
USER 10001:10001
VOLUME ["/data"]
ENTRYPOINT ["file-manager"]
CMD ["--help"]
