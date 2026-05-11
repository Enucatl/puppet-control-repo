FROM python:3.14-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        expect \
        gnupg \
        lsb-release \
        netcat-openbsd \
        openssh-client \
        wakeonlan \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /etc/apt/keyrings/hashicorp.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com $(. /etc/os-release && echo "$VERSION_CODENAME") main" > /etc/apt/sources.list.d/hashicorp.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends vault \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir niquests transitions

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin wolf

WORKDIR /app
COPY app/wolf.py /app/wolf.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh /app/wolf.py

USER 1000:1000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "/app/wolf.py"]
