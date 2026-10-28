FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
EXPOSE 8000
# Bind all interfaces inside the container; set ALLOWED_HOSTS to the public host name(s).
ENV ALLOWED_HOSTS="localhost:*"
CMD ["sh", "-c", "github-mcp-server --transport http --host 0.0.0.0 --port 8000 --allowed-hosts \"$ALLOWED_HOSTS\""]
