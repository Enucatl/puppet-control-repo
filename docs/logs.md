 Reviewed /home/user/Downloads/Logs-A-data-2026-04-30 08_51_55.csv. It contains 62 rows from 4 hosts. The real actionable items are:

  - High: FreeIPA/certmonger failures on forbearance, proxmox, and proxmox-cortex
  - Medium: Alloy tries to scrape /var/log/ipaclient-install.log and gets permission denied
  - Low/Watch: Docker DNS timeouts on docker.home.arpa
  - Low/Optional: shpchp PCI hotplug kernel noise on forbearance
  - Ignore unless user-visible: GNOME/Xwayland/session errors on forbearance, apport skipped conditions, Proxmox PCI reset warning

  ## Key Changes / Remediation

  - FreeIPA/certmonger:
      - On affected hosts, check systemctl status certmonger sssd, getcert list, and klist -kte /etc/krb5.keytab.
      - If host keytab entries are missing or stale, re-enroll the IPA client rather than masking certmonger errors.
      - Confirm freeipa.home.arpa LDAP/Kerberos reachability from proxmox.home.arpa, proxmox-cortex.home.arpa, and forbearance.home.arpa.
      - No repo change first; treat this as host state unless diagnostics show Puppet is repeatedly producing bad FreeIPA config.
  - Alloy /var/log/ipaclient-install.log:
      - Update modules/profile/templates/alloy.config.epp so Alloy does not scrape broad /var/log/*.log.
      - Replace it with an explicit allowlist of non-sensitive logs, or remove file scraping if journald already covers the needed logs.
      - Do not grant Alloy access to ipaclient-install.log; that file can contain enrollment details and should not be shipped to Loki.
  - Docker DNS timeouts:
      - Verify on docker.home.arpa: resolvectl query hcv.home.arpa AAAA, resolvectl query tracker.openbittorrent.com AAAA, and journalctl -u
        systemd-resolved --since today.
      - If failures continue, configure Docker to use the LAN resolver directly instead of the 127.0.0.53 systemd-resolved stub.
      - Treat the tracker.openbittorrent.com errors as low-priority unless containers are reporting DNS failures.
  - shpchp PCI hotplug messages:
      - If forbearance does not need PCI hotplug, add a Puppet-managed modprobe blacklist for shpchp.
      - If the host uses hotplug/Thunderbolt/eGPU-style PCI behavior, leave it alone; it is noisy but not clearly harmful.
  - Non-actionable noise:
      - GNOME tracker, geolocation portal, Broken pipe, Xwayland, PackageKit, and Evolution messages are tied to a user session reset around
        2026-04-30T06:39Z.
      - apport-autoreport skipped condition is expected when autoreporting is disabled.
      - Proxmox pvedaemon PCI reset ioctl warning is only actionable if a VM/device reset actually failed.

  ## Test Plan

  - After FreeIPA remediation, getcert list shows no stuck/failed requests and journalctl -u certmonger --since today is clean.
  - After Alloy config change, run Puppet with /opt/puppetlabs/bin/puppet agent -t, restart Alloy, and confirm no new permission denied
    tailer errors.
  - Confirm Loki still receives journald logs from each host after the Alloy change.
  - For Docker DNS, run repeated resolvectl query checks and confirm Docker logs no longer show resolver timeouts over the next day.
  - If blacklisting shpchp, reboot forbearance once and confirm the kernel errors disappear without breaking required PCI devices.

  ## Assumptions

  - The goal is operational triage first, not immediate broad repo cleanup.
  - FreeIPA/certmonger is more important than suppressing desktop-session noise.
  - Sensitive install logs should not be made readable just to satisfy Alloy.
  - No code changes should be made until the host-side FreeIPA diagnostics confirm whether this is configuration drift or transient service
    state.
