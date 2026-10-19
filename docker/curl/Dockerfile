FROM ubuntu:26.04@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944 AS build

ENV DEBIAN_FRONTEND=noninteractive
RUN sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends dpkg-dev devscripts patch \
    && apt-get build-dep -y curl=8.18.0-1ubuntu2.8
WORKDIR /build
RUN apt-get source curl=8.18.0-1ubuntu2.8
COPY openssl-channel-binding.patch /tmp/channel-binding.patch
WORKDIR /build/curl-8.18.0
RUN patch --batch --fuzz=0 -p1 < /tmp/channel-binding.patch \
    && DEBFULLNAME='Atlas infrastructure' DEBEMAIL='root@atlas.home.arpa' \
       dch --local +atlas 'Backport curl b0f6e9a: support digestless ML-DSA channel bindings.' \
    && DEB_BUILD_OPTIONS=nocheck dpkg-buildpackage -b -us -uc -j4 \
    && mkdir /artifacts \
    && cp /build/curl_8.18.0-1ubuntu2.8+atlas1_amd64.deb \
          /build/libcurl4t64_8.18.0-1ubuntu2.8+atlas1_amd64.deb /artifacts/ \
    && cd /artifacts && sha256sum ./*.deb > SHA256SUMS
RUN cp /build/curl_8.18.0-1ubuntu2.8+atlas1_amd64.buildinfo /artifacts/

FROM scratch
COPY --from=build /artifacts/ /
