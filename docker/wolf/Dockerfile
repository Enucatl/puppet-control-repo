FROM python:3.14-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        expect \
        netcat-openbsd \
        openssh-client \
        wakeonlan \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir niquests transitions

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin wolf
RUN install -d -o wolf -g wolf /state

WORKDIR /app
COPY app/wolf.py /app/wolf.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh /app/wolf.py

USER wolf
VOLUME ["/state"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz >/dev/null || exit 1
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "/app/wolf.py"]
