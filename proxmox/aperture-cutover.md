# Aperture physical desktop cutover

This procedure targets only Forbearance 300 and Aperture 310 on
`proxmox.home.arpa`. It preserves disks, MAC addresses, VM UUIDs and enrollment.
The Cortex/Cognition tools target a different hypervisor and must not be used.

Run the committed `aperture-handover.py` as root on Proxmox. `prepare` saves the
original configurations under `/root/aperture-cutover`, refuses to overwrite
them, and leaves running guests untouched. `apply` and `rollback` require both
VMs stopped, reject pending changes and unexpected identities, and leave both
stopped. They modify only PCI assignments, memory, onboot, startup and VGA.
A failed apply leaves the saved configurations available for explicit rollback.

```bash
python3 /root/aperture-handover.py prepare
qm shutdown 310 --timeout 180
qm shutdown 300 --timeout 180
python3 /root/aperture-handover.py apply
qm start 310
```

Do not force-stop a guest if shutdown fails: diagnose it first. Do not start a
VM after a failed transfer until ownership has been inspected or rolled back.
The four original assignments (09:00.0, .1, .3, .4) move with their exact options;
functions .2, .5 and .6 remain on the host. Aperture uses `vga=none` with the
existing `x-vga=1` primary GPU flag and `startup=order=3`.

Rollback, from Atlas with its independent local administrative key:

```bash
ssh -o IdentityAgent=none -o ForwardAgent=no -o StrictHostKeyChecking=yes \
  -i /home/user/.ssh/aperture-proxmox root@proxmox.home.arpa \
  'qm shutdown 310 --timeout 180 && qm set 310 --ide2 local-zfs:vm-310-cloudinit,media=cdrom && python3 /root/aperture-handover.py rollback && qm start 300'
```

If Aperture is already stopped, omit `qm shutdown 310`. Reattaching the retained
cloud-init volume restores the saved configuration required by the rollback
guard; it does not change the system disk. Forbearance must remain
stopped until rollback has restored its assignments. The saved original
Aperture configuration restores virtual graphics and 8192 MiB; Forbearance
regains its original memory, autostart, and hardware. Neither VM is deleted.

Administrative access uses an Atlas-local Ed25519 key authorized on Proxmox
root and Atlas user_l, and works with `IdentityAgent=none`; it does not depend
on Forbearance's forwarded agent. The private key and original VM records stay
outside Git. GitHub publication during the cutover uses a preauthenticated
Atlas user_l SSH control connection. It can expire; recovery access does not
depend on that connection.

Physical monitor, keyboard/mouse, audio and interactive login require operator
confirmation. Application preferences and licences are not comprehensively
verified. No whole-home synchronization is performed.

## Completed cutover: 2026-10-20

Handover implementation `451316b` passed five focused tests, including active
VM rejection and recovery after partial application. It was published through
`atlas-publish.sh` before shutdown. Both guests shut down gracefully; no force
stop was used. Final-state assertions and Proxmox configuration reads confirm:

| Guest | State | Memory | Autostart | PCI ownership |
| --- | --- | --- | --- | --- |
| Forbearance 300 | stopped | 4096 MiB | disabled | none |
| Aperture 310 | running | 16384 MiB | enabled, order 3 | 09:00.0, .1, .3, .4 |

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

After shutdown, Forbearance's forwarded agent disappeared as expected. Atlas
administration still works with the local key and `IdentityAgent=none`. The
final results commit is signed with that Atlas-local key rather than the
unavailable forwarded personal signer; persistent Git signing settings are
unchanged. Publication uses the preauthenticated GitHub control connection.

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
rollback, along with `/root/aperture-cutover/310-before-cidata-removal.conf`.
Do not delete that seed volume while the documented rollback still uses it.
