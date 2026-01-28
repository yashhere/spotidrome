# Spotidrome Justfile

# Variables
image := "git.lan/yash/spotidrome:latest"
platform := "linux/arm64"

# List available recipes
default:
    @just --list

# Run development server locally
run:
    uv run uvicorn app.api:app --host 0.0.0.0 --port 8095 --reload

# Build and push Docker image
release:
    docker buildx build --platform {{platform}} -t {{image}} --push .

# Clean up temporary files
clean:
    find . -type d -name "__pycache__" -exec rm -rf {} +
    find . -name ".DS_Store" -delete
    rm -f *.lrc
