# Aether: one image serves the API, the web UI and the worker (different commands).
# The UI is built inside Docker unless frontend/dist already contains a build (release zips ship one,
# so no npm registry access is needed). Force a rebuild with: docker compose build --build-arg WEB=build
ARG PYTHON_IMAGE=python:3.12-slim
ARG NODE_IMAGE=node:20-alpine

FROM ${NODE_IMAGE} AS web
ARG WEB=auto
WORKDIR /web
COPY frontend/ ./
# npm can exit 0 after an interrupted install ("Exit handler never called"); retry until the toolchain exists.
RUN if [ "$WEB" != "build" ] && [ -f dist/index.html ]; then echo "Using prebuilt UI from frontend/dist"; else \
      rm -rf node_modules dist && npm config set fetch-retries 5 && npm config set fetch-retry-maxtimeout 120000 && \
      for i in 1 2 3; do npm ci --no-audit --no-fund && test -x node_modules/.bin/tsc && test -x node_modules/.bin/vite && break; \
        echo "npm ci incomplete, retrying ($i)"; rm -rf node_modules; sleep 5; done && \
      test -x node_modules/.bin/tsc && npm run build; fi && test -f dist/index.html

FROM ${PYTHON_IMAGE} AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    AETHER_STATIC_DIR=/app/static AETHER_TLS_TRUST=system
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.25 && useradd --create-home --uid 10001 aether
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o requirements.txt && uv pip install --system --no-cache -r requirements.txt
COPY backend/ ./
RUN uv pip install --system --no-cache --no-deps .
COPY --from=web /web/dist /app/static
COPY config/ /config/
USER aether
EXPOSE 8000 9101
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)" || exit 1
CMD ["aether", "serve", "--host", "0.0.0.0", "--port", "8000"]
