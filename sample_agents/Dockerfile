ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY sample_agents/ ./sample_agents/
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 app
USER app
EXPOSE 8100
CMD ["uvicorn", "sample_agents.app:app", "--host", "0.0.0.0", "--port", "8100"]
