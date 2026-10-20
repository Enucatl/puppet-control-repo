# Official Grafana development image on Atlas

Atlas uses the official Grafana 13.3.0-39395647570 AMD64 image from
[upstream build 39395647570](https://github.com/grafana/grafana/actions/runs/39395647570),
commit `42bf57e610339412007cc04929362d2fddb4e8fb`. Its extracted backend uses
Go 1.27.3, allowing verified TLS to Atlas Authelia's ML-DSA endpoint.

Run `bash docker/grafana/load-official.sh` from an authenticated GitHub CLI
workstation to download the image archive, verify its supplied SHA-256 checksum,
and load it on Atlas. GitHub artifact retention is short; preserve the loaded
image until a suitable registry release replaces it. This is a 13.3 prerelease,
not a rebuild of 13.2.4. No custom image build is needed.

The versioned observability preparation pins image ID
`sha256:62ded2143ad839ed656476b68592849fc76144d4a52af90d752492a7f45fdc7d`.
Use Compose with `--pull never`. Preserve the pre-upgrade data archive before
starting Grafana: database migrations can prevent downgrade in place.
