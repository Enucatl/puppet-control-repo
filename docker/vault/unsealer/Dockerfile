FROM alpine:3.23

RUN apk add --no-cache bash ca-certificates curl jq

RUN mkdir /recovery /bootstrap /unseal && \
    chown 100:100 /recovery /bootstrap /unseal && \
    chmod 700 /recovery /bootstrap /unseal

USER 100:100

ENTRYPOINT ["/bin/bash"]
