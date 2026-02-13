FROM python:3.13-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first (cache layer)
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy application code
COPY app.py benchmark.py auth.py db.py keyvault.py index.html config.yaml migrate_to_multiuser.py ./

# Create data directory
RUN mkdir -p data

# Non-root user
RUN useradd -m -s /bin/bash bench && chown -R bench:bench /app
USER bench

EXPOSE 8501

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/healthz')" || exit 1

CMD ["uv", "run", "python", "app.py", "--host", "0.0.0.0"]
