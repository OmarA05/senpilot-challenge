# Playwright's image ships Chromium and its system libraries; keep the tag in sync with the playwright version below.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOWNLOAD_DIR=/app/downloads

COPY pyproject.toml ./
COPY src ./src
RUN pip install "playwright==1.64.1" . \
    && mkdir -p /app/downloads && chown pwuser:pwuser /app/downloads

USER pwuser
# Secrets come from the environment (docker run --env-file .env), never baked into the image.
ENTRYPOINT ["python", "-m", "uarb_agent.main"]
CMD ["run"]
