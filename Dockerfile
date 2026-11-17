FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    STEALTHFOX_MCP_TRANSPORT=http \
    STEALTHFOX_MCP_HOST=0.0.0.0 \
    STEALTHFOX_MCP_PORT=8000 \
    STEALTHFOX_HEADLESS=1

# Install Linux X11/GTK runtime libraries and xvfb required by headless Firefox
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        curl \
        ca-certificates \
        xvfb \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libc6 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libexpat1 \
        libfontconfig1 \
        libgbm1 \
        libgcc1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libstdc++6 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxcursor1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxi6 \
        libxrandr2 \
        libxrender1 \
        libxss1 \
        libxtst6 \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast deterministic dependency resolution
RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml LICENSE README.md /app/
COPY src /app/src
COPY server_wrapper.py /app/server_wrapper.py

RUN uv pip install --system --no-cache . && \
    mkdir -p /data/profile /data/downloads

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8000/healthz || exit 1

ENTRYPOINT ["python", "-m", "stealth_wrapper"]
