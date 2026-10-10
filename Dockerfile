FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.9.16 /uv /bin/uv
WORKDIR /app
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --no-build-package gradbot && useradd --uid 10001 --create-home agent
USER agent
EXPOSE 8082
HEALTHCHECK --interval=30s --timeout=3s CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8082/healthz', timeout=2)"]
CMD ["/app/.venv/bin/uvicorn", "familyshield.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8082", "--ws-max-size", "32768", "--ws-max-queue", "16", "--timeout-graceful-shutdown", "15"]
