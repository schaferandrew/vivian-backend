FROM python:3.13-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy and install shared package first
COPY packages/shared /packages/shared
RUN pip install -e /packages/shared

# Copy and install MCP server (API launches this via subprocess)
COPY apps/mcp-server /mcp-server
RUN pip install -e /mcp-server

# Copy and install test MCP server
COPY apps/test-mcp-server /test-mcp-server
RUN pip install -e /test-mcp-server

# Copy and install API
COPY apps/api /app
RUN pip install -e .

# Copy entrypoint script
COPY apps/api/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Non-root user: one backend, many clients; avoid running as root
RUN groupadd --gid 1000 vivian \
    && useradd --uid 1000 --gid vivian --shell /bin/bash --create-home vivian \
    && mkdir -p /tmp/vivian-uploads \
    && chown -R vivian:vivian /app /tmp/vivian-uploads

USER vivian

EXPOSE 8000

CMD ["/entrypoint.sh"]
