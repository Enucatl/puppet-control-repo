# Aperture completed desktop cutover

This record covers Forbearance 300 and Aperture 310 on
`proxmox.home.arpa`. The migration is complete and the one-off hardware
handover and desktop recovery helpers have been retired. Original VM
configuration records remain outside Git under `/root/aperture-cutover`;
the former apply and rollback commands are no longer an operational procedure.

## Completed cutover: 2026-10-20

Handover implementation `451316b` passed five focused tests, including active
VM rejection and recovery after partial application. It was published before
shutdown. Both guests shut down gracefully; no force stop was used. Final-state
assertions and Proxmox configuration reads confirm:

| Guest | State | Memory | Autostart | PCI ownership |
| --- | --- | --- | --- | --- |
| Forbearance 300 | stopped | 4096 MiB | disabled | none |
| Aperture 310 | running | 16384 MiB | enabled, order 3 | 09:00.0, .1, .3, .4 |

The four PCI assignments retained their exact options; functions .2, .5 and .6
remained on the host. Aperture uses `vga=none` with the existing `x-vga=1`
primary GPU flag.

Both disks, EFI disks, identities and network settings were preserved. Saved
hashes of every other VM/template configuration on this hypervisor match after
the cutover; router, Docker and Atlas retain their running states and PIDs.
No Cortex/Cognition operation was performed.

Aperture's agent and DMI report the correct hostname and 16 GB installed memory.
Linux reports 15407 MiB usable after guest reservations. Radeon uses `amdgpu`,
AMD audio uses `snd_hda_intel`, and both transferred USB controllers use
`xhci_hcd`. Logitech receiver/webcam, Keychron receiver and HyperX wireless
headset enumerate. HDMI reports a connected Samsung monitor; GDM has an active
seat0 greeter. These observations do not prove physical image/input/audio.

GDM, SSSD, OpenVox and QEMU guest agent are active with no failed systemd units.
The new production catalog ran with zero failed resources/events. IPA user
lookup, host-keytab Kerberos TGT and Atlas service ticket acquisition pass.
Both Atlas mounts are NFS 4.2 with `sec=krb5p`: scratch directory reads and
Docker metadata reads pass. Docker's existing 2770 permissions deny root a
directory listing, as expected. Brother_MFC_L2750DW remains the default printer.

Proxmox logged `Inappropriate ioctl for device` attempting to reset 09:00.0,
then continued. The host exposes only the `bus` reset method. The guest loaded
DMUB/VCN firmware, initialized SMU/Display Core and the AMD framebuffer, and GDM
opened the AMD device with atomic modesetting. No guest GPU hang or required
firmware load failure was observed. Optional RAS/RAP/SECUREDISPLAY TA firmware
messages and the unused emulated Intel audio device's no-codec message did not
prevent transferred devices from initializing. No reset workaround or additional
PCI function was assigned. Future reset/reboot reliability remains untested.

Log checks used the current Atlas Loki read endpoint; the old Docker read
endpoint from the logs skill failed CA verification. The Proxmox selector
returned no matching entries; the reset warning above comes from the captured
startup output. Exact selectors, time range and sanitized results are in
[the cutover evidence](../docs/aperture-cutover-results.json).

After shutdown, Forbearance's forwarded agent disappeared as expected. The
temporary local key maintained access until Aperture's normal key was usable.
The three commits initially signed with the temporary key were subsequently
re-signed with the normal key and production history was force-published using
exact leases. Persistent Git signing settings are unchanged.

The temporary key's authorizations on Atlas user_l and Proxmox root, and its
private/public files on Atlas, were removed. The separately authorized
`u0_a287@localhost` entry was also removed from Proxmox. The normal personal key,
Proxmox cluster key, guest identities and rollback records were retained.

The operator subsequently confirmed a working physical desktop and successful
interactive login. The migrated `user` password hash initially lacked Kerberos
keys; one verified TLS LDAP bind with the existing password generated them.
No password reset or authentication policy change was needed. Audible audio,
personal settings and licences have not been comprehensively verified.

After cloud-init reported `done` without errors, the operator requested removal
of `cidata`. Its mounted filesystem was unmounted and the media ejected without
interrupting the active desktop session. QEMU reports `ide2: [not inserted]`,
and guest reads of `/dev/sr0` report no medium. IDE slot deletion is pending the
next shutdown; boot remains `order=scsi0`. The 4 MiB seed volume is retained for
recovery evidence, along with
`/root/aperture-cutover/310-before-cidata-removal.conf`.
