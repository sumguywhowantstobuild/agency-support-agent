# check=error=true
# The agent's container: the same image runs on a PC (compose.dev.yaml) and on a server (deploy/compose.yaml).

# Stage 1: install the exact libraries from uv.lock, then our code.
# Tag and digest: the digest pins the exact bytes (tags get pushed again); updated monthly (SETUP section 12).
FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Libraries first, our code second: a code change reuses the cached library step, so rebuilds take seconds.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# Stage 2: only Python, the libraries and our code. No uv, no source folder, no test tools, and no install tools:
# the base image's pip, setuptools and wheel never run here, but the scanner flags what they carry.
FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce
RUN pip uninstall --yes --no-cache-dir setuptools wheel pip && useradd --system --uid 10001 --no-create-home agent
COPY --from=build --chown=agent:agent /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER agent
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8)"]
# 0.0.0.0 = listen inside the container's own network; compose decides who outside can reach it.
CMD ["uvicorn", "support_agent.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]