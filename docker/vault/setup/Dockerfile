FROM hashicorp/vault:latest

USER root

RUN apk add --no-cache jq && \
    mkdir -p /certificates && \
    chown 100:100 /certificates

USER 100:100
