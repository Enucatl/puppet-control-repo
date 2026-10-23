FROM ghcr.io/enucatl/openvox-server@sha256:5659cb80fcd29b22b678fe761ccdc4a63777dd738e8dce77a8aad16ac1e3cf6a

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git util-linux \
    && git config --system --add safe.directory /git/control.git \
    && rm -rf /var/lib/apt/lists/* \
    && /opt/puppetlabs/puppet/bin/gem install --no-document r10k --version 5.0.3 \
    && mkdir -p /git /var/cache/r10k /etc/puppetlabs/code /trust \
    && chown puppet:puppet /git /var/cache/r10k /etc/puppetlabs/code /trust
USER puppet
