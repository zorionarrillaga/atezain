# The deployed image (PLAN.md §4.5). Built and run nowhere yet: there is no Docker on the author's
# machine, so this file has never been executed and STATUS.md says so.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY ops/requirements.txt /app/ops/requirements.txt
RUN pip install --no-cache-dir -r ops/requirements.txt

COPY policy/ /app/policy/
COPY agent/ /app/agent/
COPY records/ /app/records/
COPY api/ /app/api/
COPY adapters/ /app/adapters/

# a non-root user owns the state directory and nothing else
RUN useradd --system --uid 10001 atezain && mkdir -p /app/var && chown -R atezain /app/var
USER atezain

ENV ATEZAIN_STATE_DIR=/app/var ATEZAIN_ADAPTER=invoices-es PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/healthz', timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
