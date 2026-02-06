FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install shared package first
COPY packages/shared /packages/shared
RUN pip install -e /packages/shared

# Copy and install API
COPY apps/api /app
RUN pip install -e .

# Non-root user: one backend, many clients; avoid running as root
RUN groupadd --gid 1000 vivian \
    && useradd --uid 1000 --gid vivian --shell /bin/bash --create-home vivian \
    && mkdir -p /tmp/vivian-uploads \
    && chown -R vivian:vivian /app /tmp/vivian-uploads

USER vivian

EXPOSE 8000

CMD ["python", "-m", "vivian_api.main"]
