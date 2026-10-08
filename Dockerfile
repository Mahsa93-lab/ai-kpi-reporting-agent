# MCP server with the KPI tools (Streamable HTTP on port 8100)
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 KPI_DATA_DIR=/app/data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp_server ./mcp_server
COPY data/*.parquet ./data/
RUN useradd --create-home --uid 10001 mcp
USER mcp
EXPOSE 8100
CMD ["python", "-m", "mcp_server.server"]
