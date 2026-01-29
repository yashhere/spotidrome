FROM python:3.12-slim

# Install system dependencies including deno for yt-dlp-ejs
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    wget \
    git \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install deno (required for yt-dlp-ejs to decipher YouTube n/sig values)
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh
ENV PATH="/usr/local/bin:$PATH"

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Install dependencies using uv (use container's Python, copy mode to avoid host symlinks)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --python python3 --link-mode=copy

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
