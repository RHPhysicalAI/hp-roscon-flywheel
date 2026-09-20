# This project was developed with assistance from AI tools.
# Build context: src/eval-dashboard
# Multi-arch base (amd64 + aarch64) that pulls without a registry login.
FROM registry.access.redhat.com/ubi9/python-312

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY eval_dashboard/ ./eval_dashboard
COPY config/ ./config

# Runs as the base image's non-root user. /app stays root-owned and nothing
# is written at run time, bytecode caches included.
ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080
EXPOSE 8080

CMD ["python", "-m", "eval_dashboard.main"]
