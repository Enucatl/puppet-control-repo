FROM ghcr.io/enucatl/openvox-server@sha256:b27583dfa1f3adc3f96bdcee192b783e0044cfb02837456d459b8ca6eca59b97

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git util-linux \
    && git config --system --add safe.directory /git/control.git \
    && rm -rf /var/lib/apt/lists/* \
    && /opt/puppetlabs/puppet/bin/gem install --no-document r10k --version 5.0.3 \
    && mkdir -p /git /var/cache/r10k /etc/puppetlabs/code /trust \
    && chown puppet:puppet /git /var/cache/r10k /etc/puppetlabs/code /trust
USER puppet
