FROM ubuntu:26.04@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944 AS build

RUN apt-get update && apt-get install -y --no-install-recommends patch
WORKDIR /build
RUN apt-get download python3-ipalib=4.13.1-1 \
    && dpkg-deb --raw-extract python3-ipalib_4.13.1-1_all.deb patched
COPY public-key-info.patch /build/
RUN cd patched \
    && patch --batch --fuzz=0 -p1 < ../public-key-info.patch \
    && sed -i 's/^Version: 4.13.1-1$/Version: 4.13.1-1+atlas1/' DEBIAN/control \
    && sed -i '/^Depends:/s/$/, python3-packaging/' DEBIAN/control \
    && sed -i '\|usr/lib/python3/dist-packages/ipalib/x509.py$|d' DEBIAN/md5sums \
    && md5sum usr/lib/python3/dist-packages/ipalib/x509.py >> DEBIAN/md5sums \
    && mkdir /artifacts \
    && dpkg-deb --build --root-owner-group . /artifacts/python3-ipalib_4.13.1-1+atlas1_all.deb \
    && cp ../python3-ipalib_4.13.1-1_all.deb /artifacts/ \
    && cd /artifacts && sha256sum ./*.deb > SHA256SUMS

FROM scratch
COPY --from=build /artifacts/ /
