# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- build stage
# Resolve the locked dependencies and the momentum package into a virtualenv.
FROM python:3.12.15-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src

# Dependencies first so this layer is cached until uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# Brotli copies of Streamlit's frontend bundle, served by momentum.server to
# browsers that accept br. Quality 11 is too slow per request but fine here.
# brotli lives in a throwaway build venv, never in the runtime image.
COPY scripts/precompress.py ./scripts/
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_PROJECT_ENVIRONMENT=/opt/build-venv \
    uv sync --locked --only-group precompress --no-install-project \
    && /opt/build-venv/bin/python scripts/precompress.py \
       "$(/opt/venv/bin/python -c 'import pathlib, streamlit; print(pathlib.Path(streamlit.__file__).parent / "static")')"

# ---------------------------------------------------------------- runtime stage
FROM python:3.12.15-slim

LABEL org.opencontainers.image.title="momentum" \
      org.opencontainers.image.description="Streamlit dashboard for global stocks and indices" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/Arya-9989/Momentum-Live-Stock-Tracker"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none \
    MOMENTUM_DEMO=0

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app streamlit_app.py serve.py ./
COPY --chown=app:app .streamlit ./.streamlit

USER app
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"]

# serve.py wraps streamlit_app.py in st.App: gzip for the static bundle, a real
# robots.txt, title and description (see src/momentum/server.py).
CMD ["streamlit", "run", "serve.py"]
