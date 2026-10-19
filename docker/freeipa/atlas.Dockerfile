FROM ghcr.io/enucatl/freeipa-server@sha256:80024f5e76ce4eedd986eb9e7ed63461cafb1bc1774386cf4ff41a259633d0cb

COPY atlas-entrypoint.py /usr/local/sbin/atlas-ipa-init.py
ENTRYPOINT ["/usr/bin/python3", "/usr/local/sbin/atlas-ipa-init.py"]
