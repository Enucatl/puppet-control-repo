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
  'qm shutdown 310 --timeout 180 && python3 /root/aperture-handover.py rollback && qm start 300'
```

If Aperture is already stopped, omit `qm shutdown 310`. Forbearance must remain
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
