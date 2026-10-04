# FreeIPA homelab tuning

Compose mounts the tuning script and an `ipa.service` drop-in read-only from
this repository. The drop-in applies tuning after `ipactl start` completes on
every container start, including after image upgrades. It configures one
IPA API worker (one request at a time), one Kerberos HTTPS proxy worker, two KDC
workers, and one idle Apache HTTP process with capacity to grow. It validates
Apache configuration and restarts only HTTP and the KDC. Failed application
restores the previous configuration.

The container healthcheck waits for `ipa.service` (including its tuning hook)
to become active before checking HTTPS.

For manual application, run `bash docker/freeipa/tune-homelab.sh` on the Docker
host. The same script runs inside the container at startup.

Run `bash docker/freeipa/check-homelab-tuning.sh` to check the live settings and
worker counts. Authentication and certificate checks should also be performed
after applying or upgrading.

Settings persist in FreeIPA's `/data` volume. The startup hook reapplies them if
an upgrade regenerates configuration. It does not restart services when the
settings already match.

Original files are retained under `/data/homelab-tuning-backup.*`. To undo a
change, remove the drop-in mount from Compose and recreate the container first,
then restore `ipa.conf`, `ipa-kdc-proxy.conf`, and `krb5kdc` to their original
paths (follow their symlinks), restore `zz-homelab-mpm.conf` if it was backed up
or remove it otherwise, validate with `httpd -t`, and restart `httpd krb5kdc`.

## Measured result (2026-10-04)

After functional warmup, five samples two seconds apart showed Docker-style
memory usage (cgroup total minus inactive file cache) falling from 1,034.7 MiB
to 697.4 MiB: 337.3 MiB saved, or 32.6%. Raw cgroup means fell from 1,081.4 MiB
to 745.8 MiB. The after measurement included 60 seconds of stabilization.

Passed: all FreeIPA services running, host-keytab Kerberos login and service
tickets, certificate-verified LDAP/HTTPS, authenticated IPA API and user/group/
HBAC/sudo reads, and Kerberos login/service tickets through the HTTPS KDC proxy.
The live tuning check passed, and a second application made no changes.
Password login, new host enrollment, and certificate issuance/renewal were not
exercised. Savings are an idle snapshot, not a guarantee under sustained load.
