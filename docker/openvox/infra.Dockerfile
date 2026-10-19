FROM ghcr.io/enucatl/openvox-server@sha256:7b28e8e6937c5e5d1550f86fa540d711adcd0e31e393ac1b2c50821fd598b12a

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
