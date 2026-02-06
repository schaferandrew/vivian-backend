FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy and install MCP server
COPY apps/mcp-server /app
RUN pip install -e .

# Run the MCP server
CMD ["python", "-m", "vivian_mcp.server"]
