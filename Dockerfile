# API image (FastAPI + LangGraph). Multi-stage: wheels are built once, the runtime stage has no compilers.
# Works on linux/arm64 (EC2 t4g) and linux/amd64. One uvicorn process: the background jobs (inbox poll, reminders,
# nudges) run in this process only.
FROM python:3.13-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements-prod.txt constraints-prod.txt ./
RUN pip wheel --wheel-dir /wheels -r requirements-prod.txt -c constraints-prod.txt

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RIGHTSTAFF_DB=/app/data/demo.db RIGHTSTAFF_UPLOADS=/app/data/uploads PRINT_TIMINGS=0
RUN useradd --uid 1000 --create-home app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links /wheels /wheels/*.whl && rm -rf /wheels
WORKDIR /app
# Only code and synthetic fixtures (see .dockerignore): no data/, uploads/, timings/, .env or reference extractions.
COPY --chown=app:app . .
RUN mkdir -p /app/data && chown app:app /app/data
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).read() == b'ok' else 1)"
CMD ["uvicorn", "api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
