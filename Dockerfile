FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV SAMPLE_MODE=true

# Runtime dependencies only. Test tooling lives in requirements-dev.txt.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Run as an unprivileged user. Only the directories the API writes to are owned by it.
RUN useradd --create-home --uid 10001 phoenix
COPY . .
RUN mkdir -p /app/logs /app/data/hmem /app/run_artifacts \
    && chown -R phoenix:phoenix /app/logs /app/data/hmem /app/run_artifacts
USER phoenix

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)" || exit 1

CMD ["python", "main.py"]
