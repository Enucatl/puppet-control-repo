FROM hashicorp/vault@sha256:e0acd3c681bdf57f002d7214e5d050b876e1704678005c6b0406dd96a65be919 AS vault
FROM ghcr.io/enucatl/openvox-server@sha256:5659cb80fcd29b22b678fe761ccdc4a63777dd738e8dce77a8aad16ac1e3cf6a

USER root

COPY --from=vault /bin/vault /usr/local/bin/vault
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && /opt/puppetlabs/puppet/bin/gem install --no-document r10k --version 5.0.3 \
    && mkdir -p /state /trust /agent /git /var/cache/r10k /etc/puppetlabs/code \
    && chown puppet:puppet /state /trust /agent /git /var/cache/r10k /etc/puppetlabs/code

USER puppet
