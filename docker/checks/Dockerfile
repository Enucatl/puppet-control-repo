FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive UV_PYTHON=3.14
RUN apt-get update && apt-get install -y ca-certificates curl git gnupg sudo unzip systemd docker.io docker-compose-v2
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:/opt/puppetlabs/puppet/bin:${PATH}"
WORKDIR /workspace/puppet-control-repo
COPY scripts/ci/install-enrollment.sh scripts/ci/install-profiles.sh scripts/ci/
COPY Puppetfile pyproject.toml uv.lock ./
COPY provisioning/pyproject.toml provisioning/uv.lock provisioning/
COPY provisioning/src provisioning/src
RUN scripts/ci/install-enrollment.sh && scripts/ci/install-profiles.sh && uv sync --frozen
RUN git clone https://github.com/Enucatl/docker-compose-security-baseline.git /workspace/compose-security-baseline && git -C /workspace/compose-security-baseline checkout 41c57257d33d471e2ae7cc7264938944c67434fd
ENV PUPPET_CHECK_PREPARED=1
