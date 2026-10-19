FROM ghcr.io/enucatl/openvox-server@sha256:cbba872ef4b71865a8f7aacf395e4ff4ec28865995c5a9cec8023afe9d15b185

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && /opt/puppetlabs/puppet/bin/gem install --no-document r10k --version 5.0.3 \
    && mkdir -p /git /var/cache/r10k /etc/puppetlabs/code /trust \
    && chown puppet:puppet /git /var/cache/r10k /etc/puppetlabs/code /trust
COPY infra-crl-auth.rb /tmp/infra-crl-auth.rb
RUN /opt/puppetlabs/puppet/bin/ruby /tmp/infra-crl-auth.rb /etc/puppetlabs/puppetserver/conf.d/auth.conf \
    && rm /tmp/infra-crl-auth.rb
USER puppet
