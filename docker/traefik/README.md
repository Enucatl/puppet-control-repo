# Traefik with ML-DSA support

The [Enucatl Traefik fork](https://github.com/Enucatl/traefik) builds upstream
Traefik with Go 1.27 so it can serve Atlas's ML-DSA certificates. Its daily
pipeline rebases `main` onto upstream `master`, builds the complete dashboard,
and checks real ML-DSA HTTPS and mutual TLS before publishing.

Only `ghcr.io/enucatl/traefik:latest` is published. Atlas's access-stack
preparation selects that image. Record the resolved image digest at deployment;
the fork tracks upstream development changes as requested.

After starting Traefik, verify the actual TLS endpoint with an ML-DSA-capable
client and the Atlas CA bundle. Checking the build versions alone does not
verify certificate loading, trust, or the deployed ingress configuration.
