# hawk-core: Voyage (sandbox target, internal only) + safety gateway + HAWK API in one Cloud Run
# service. Cheapest MVP layout: one container, scale to zero. Voyage listens only on localhost, so
# the vulnerable sandbox is never reachable from the internet; agents still reach it only through
# the gateway process.
FROM golang:1.24-bookworm AS gateway
WORKDIR /src
COPY services/gateway/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/gateway .

FROM python:3.12-slim-trixie
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY services/voyage/ services/voyage/
COPY services/hawk/ services/hawk/
RUN uv sync --frozen --no-dev --all-packages
COPY deploy/ deploy/
COPY scripts/ scripts/
COPY --from=gateway /out/gateway /usr/local/bin/gateway
RUN mkdir -p data/traffic && /app/.venv/bin/python -m voyage.traffic_gen data/traffic/history.jsonl 120
COPY deploy/start-core.sh /start.sh
RUN chmod +x /start.sh
ENV PATH="/app/.venv/bin:$PATH" PORT=8080
CMD ["/start.sh"]
