FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Install dependencies using uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Place executables in PATH (uv installs to .venv by default)
ENV PATH="/app/.venv/bin:$PATH"

# Expose port
EXPOSE 8095

# Run with uvicorn
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8095"]
