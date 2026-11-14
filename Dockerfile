# Syntax is intentionally portable: build with `docker build -t quotehawk .`.
FROM python:3.12-slim

# Keep the pinned uv binary used to resolve the committed lockfile at build time.
COPY --from=ghcr.io/astral-sh/uv:0.8.13 /uv /uvx /bin/

ARG AGENT_VERSION=0.0.0
ENV AGENT_VERSION=${AGENT_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    QUOTEHAWK_DATABASE_PATH=/data/quotehawk.db \
    QUOTEHAWK_OTEL_TO_CLOUD=false \
    PORT=8080

WORKDIR /code

# The container is deliberately a runtime-only service. It has no credentials,
# no browser client assets, and no development dependencies.
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
RUN uv sync --frozen --no-dev

RUN addgroup --system --gid 10001 quotehawk \
    && adduser --system --uid 10001 --ingroup quotehawk --home /home/quotehawk quotehawk \
    && mkdir -p /data \
    && chown -R quotehawk:quotehawk /code /data

ENV PATH="/code/.venv/bin:${PATH}"

USER quotehawk
VOLUME ["/data"]

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/api/rfq/healthz', timeout=3).read()" || exit 1

# Disable Uvicorn's access log so request URLs or query text cannot become an
# accidental log sink; QuoteHawk emits redacted structured operational events.
CMD ["sh", "-c", "uvicorn app.fast_api_app:app --host 0.0.0.0 --port ${PORT:-8080} --no-access-log --log-level warning"]
