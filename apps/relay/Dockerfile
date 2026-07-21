FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ATC_RELAY_HOST=0.0.0.0 \
    ATC_RELAY_PORT=8743 \
    ATC_RELAY_DATABASE=/var/lib/allthecontext/relay.sqlite3

WORKDIR /opt/allthecontext
COPY pyproject.toml README.md LICENSE ./
COPY packages ./packages
RUN python -m pip install --no-cache-dir . \
    && addgroup --system --gid 10001 atc \
    && adduser --system --uid 10001 --ingroup atc --home /nonexistent --no-create-home atc \
    && mkdir -p /var/lib/allthecontext \
    && chown -R atc:atc /var/lib/allthecontext

USER 10001:10001
EXPOSE 8743
VOLUME ["/var/lib/allthecontext"]
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=4 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8743/healthz', timeout=2).read()"]

ENTRYPOINT ["atc-relay"]
