# On the other node pve-desktop.home.arpa

## Prepare the disk space, format to ZFS:

```bash
openssl rand -base64 12 > /keys/backup.key
sgdisk --zap-all /dev/disk/by-id/ata-WDC_WD4003FFBX-68MU3N0_VBGB03LF
wipefs -a !$
lsblk -o NAME,SIZE,PHY-SEC,LOG-SEC !$     # check for 4096 => ashift=12
zpool create -f -o ashift=12 tank !$
zfs create \
 -o encryption=on -o keyformat=passphrase -o keylocation=file:///keys/backup.key \
 -o recordsize=1M \
 -o compression=lz4 \
 -o atime=off \
 -o xattr=sa \
 -o acltype=posix \
 tank/backup
```

## Install Proxmox backup server
From https://pbs.proxmox.com/docs/installation.html#proxmox-backup-no-subscription-repository

```bash
echo "

Types: deb
URIs: http://download.proxmox.com/debian/pbs
Suites: trixie
Components: pbs-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg" >> /etc/apt/sources.list.d/proxmox.sources

apt update; apt -y upgrade; apt -y autoremove
apt install -y proxmox-backup-server
```

## Configure the jobs

Now that the foundation is built, we need to "link" your main Proxmox host to the backup server and handle the fact that your desktop is often turned off.

### Step 1: Get the PBS Fingerprint
Proxmox uses a certificate fingerprint to ensure the connection is secure.
1.  Log in to the **PBS Web GUI** on `pve-desktop.home.arpa:8007`.
2.  On the **Dashboard** (the first screen), look for the **Show Fingerprint** button.
3.  **Copy** that long string of characters.

---

### Step 2: Add the PBS Storage to your main host
Now, go to your main Proxmox host (`proxmox.home.arpa`):
1.  Go to **Datacenter** > **Storage** > **Add** > **Proxmox Backup Server**.
2.  **ID:** Give it a name like `PBS-Desktop`.
3.  **Server:** `pve-desktop.home.arpa` (or the IP address).
4.  **Username:** `root@pam`.
5.  **Password:** Your `pve-desktop` root password.
6.  **Fingerprint:** Paste the string you copied in Step 1.
7.  **Datastore:** `backups` (The name you gave it in the PBS GUI).
8.  **Encryption:** You can leave this "None" because you are already using **ZFS encryption** on the disk. (If you enable it here, Proxmox will encrypt the files *again* before sending them—this is safe but uses more CPU).
9.  Click **Add**.

If the status icon turns green, your main host is now talking to your backup server!

---

### Step 3: Solve the "Server is Off" problem (Wake-on-LAN)
Since `pve-desktop` is off most of the time, your scheduled backups will fail unless you wake it up.

1.  **Enable WOL in BIOS:** Ensure "Wake on LAN" or "PCIe Wake" is enabled in the BIOS of `pve-desktop`.
2.  **Install WOL on the main host:**
    Log into the shell of `proxmox.home.arpa` and run:
    ```bash
    apt update && apt install etherwake -y
    ```
3.  **Get the MAC address of `pve-desktop`:**
    On `pve-desktop`, run: `ip link`. Find the MAC address of your ethernet port (e.g., `a1:b2:c3:d4:e5:f6`).
4.  **Test it:** 
    Turn off the desktop. From the main host's shell, run:
    ```bash
    etherwake [MAC-ADDRESS]
    ```
    If the desktop boots up, you're in business.

---

### Step 4: Create your first Backup Job
On your main host (`proxmox.home.arpa`):
1.  Go to **Datacenter** > **Backup** > **Add**.
2.  **Storage:** Select `PBS-Desktop`.
3.  **Schedule:** Set a time when you are likely to have the desktop on (or set it to 3 AM and use a script to wake it).
4.  **Selection Mode:** Select the VMs/Containers you want to protect.
5.  **Retention tab:** Set how many backups you want to keep (e.g., Keep last 7 days, 4 weeks, 2 months). PBS is so efficient you can usually keep much more than traditional backups.

---

### Step 5: Automating the Wake-up (The "Pro" move)
You can tell your main Proxmox host to wake the desktop automatically before the backup starts.
1.  On the main host, create a small script in `/usr/local/bin/wake-pbs.sh`:
    ```bash
    #!/bin/bash
    etherwake [MAC-ADDRESS-OF-DESKTOP]
    sleep 120 # Wait 2 minutes for it to boot and mount ZFS
    ```
2.  Make it executable: `chmod +x /usr/local/bin/wake-pbs.sh`
3.  Add a **Cron Job** (`crontab -e`) to run this script 5 minutes before your scheduled Proxmox backup.

---

**You are now set up!** Your first backup will take a while as it sends the full data, but every backup after that will be incredibly fast (incremental) and deduplicated.
