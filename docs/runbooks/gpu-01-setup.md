# gpu-01 Setup Runbook — LUKS + Docker + SSH Tunnels

**Server:** Hetzner GEX44 #2963286 | IP: 5.9.10.215 | FSN1-DC13 (Germany)
**GPU:** RTX 4000 SFF Ada Generation (20 GB GDDR6)
**Disks:** 2× 1.7 TB NVMe (RAID1 via mdadm)
**OS target:** Ubuntu 24.04 LTS + LUKS full-disk encryption

**SPEC:** SPEC-GPU-001 v2.0

---

## Status

| Phase | Status | Datum |
|---|---|---|
| Phase 0: LUKS OS installatie | ✅ Gereed | 2026-03-30 |
| Phase 1: Docker + GPU services | ⏳ Pending | — |
| Phase 2: SSH tunnels op core-01 | ⏳ Pending | — |
| Phase 3: Core-01 consumer migratie | ⏳ Pending | — |
| Phase 4: Monitoring + rollback drill | ⏳ Pending | — |

---

## Sessielog / Wat Geleerd

### 2026-03-29 — Installimage configuratiefouten gevonden

#### Fout 1: Verkeerd encryptie-keyword (`encrypt` i.p.v. `crypt`)

De installimage config gebruikte `encrypt` op de PART-regel:
```
PART lvm  vg0  all  encrypt   ← FOUT
```

Het juiste keyword is `crypt`:
```
PART lvm  vg0  all  crypt     ← CORRECT
```

**Gevolg:** `validate_vars()` in `functions.sh` faalt stil. `dialog` probeert een foutvenster te tonen, maar zonder TTY geeft `dialog` exit code 1, wat `CANCELLED=true` triggert. Autosetup.sh print dan "Cancelled." en stopt.

**Bewijs:** [Hetzner installimage source - functions.sh](https://github.com/hetzneronline/installimage/blob/master/functions.sh) — `PART_CRYPT[$i]` wordt ingesteld op het 5e veld van de PART-regel. Validatie checkt specifiek op `crypt`.

#### Fout 2: Verwarring exit code 0

De eerste poging draaide met `nohup ... &` — SSH-commando exited 0 (de achtergrondstart slaagde), maar het eigenlijke `installimage` process exited 1 ("Cancelled."). Gebruik altijd foreground met `TERM=xterm installimage -a -c config.conf`.

#### Bevinding 3: LV volgorde — `all` MOET als laatste

**Kritieke regel:** Een LV met grootte `all` (alle resterende ruimte) MOET de LAATSTE LV in de VG zijn.

```bash
# FOUT — root (all) staat vóór swap
LV vg0 root  /     ext4  all    ← FOUT: all niet als laatste
LV vg0 swap  swap  swap  32G

# CORRECT — swap (fixed) eerst, root (all) als laatste
LV vg0 swap  swap  swap  32G
LV vg0 root  /     ext4  all    ← CORRECT: all als laatste
```

installimage geeft anders: `LV size 'all' has to be on the last LV in VG vg0`

#### Bevinding 4: Debug log bij "Cancelled."

Wanneer installimage "Cancelled." geeft, kijk in `/root/debug.txt`:
```bash
tail -30 /root/debug.txt   # Bevat de echte foutmelding
```

Zonder dit bestand te lezen is het onmogelijk te weten wat er fout ging (dialog-foutvenster is onzichtbaar zonder TTY).

#### Bevinding 5: CRYPTPASSWORD in /installimage.conf

Na een succesvolle LUKS-installatie plaatst installimage een kopie van de config (inclusief CRYPTPASSWORD in plaintext) in `/installimage.conf` op het geïnstalleerde systeem. **Dit bestand moet direct na installatie worden verwijderd.**

---

### 2026-03-30 — Dropbear remote unlock sessie

#### Bevinding 6: `cryptroot-unlock` leest NIET van stdin

De standaard aanpak `echo "passphrase" | ssh ... cryptroot-unlock` werkt NIET omdat `cryptroot-unlock` leest van `/dev/console` (de echte terminal), niet van stdin.

Resultaat: `cryptsetup: cryptsetup failed, bad password or options?` of stille mislukking.

#### Bevinding 7: De juiste non-interactieve methode is `passfifo`

In Ubuntu initramfs wacht cryptsetup op een named FIFO pipe: `/lib/cryptsetup/passfifo`.

**Correcte niet-interactieve unlock:**
```bash
echo -ne "LUKS_PASSPHRASE" > /lib/cryptsetup/passfifo
```

Let op: `-ne` is essentieel — `-n` voorkomt trailing newline, `-e` verwerkt escape sequences.

**Werkende aanpak via SSH:**
```bash
ssh -p 2222 -i /opt/klai/gpu-unlock-key root@5.9.10.215 \
  "echo -ne 'LUKS_PASSPHRASE' > /lib/cryptsetup/passfifo"
```

Dit returnt exit 0 zonder output — dat is correct. De server begint dan te booten.

#### Bevinding 8: ed25519 sleutels werken WEL in Ubuntu 24.04

Oudere documentatie zegt dat Dropbear initramfs alleen RSA ondersteunt, maar Ubuntu 24.04's `dropbear-initramfs` ondersteunt ook ed25519. De unlock-sleutel die wij gebruiken (ed25519) werkt.

#### Bevinding 9: SSH host key conflict na nieuw OS

Na een nieuwe installatie heeft de server een nieuw SSH host key. Verwijder de oude entry vóór je verbindt:
```bash
ssh-keygen -R 5.9.10.215
ssh -o StrictHostKeyChecking=accept-new root@5.9.10.215
```

---

## Correcte installimage Config

```bash
CRYPTPASSWORD VERVANG_MET_ECHTE_PASSPHRASE    # BOVENAAN
DRIVE1 /dev/nvme0n1
DRIVE2 /dev/nvme1n1
SWRAID 1
SWRAIDLEVEL 1
BOOTLOADER grub
HOSTNAME gpu-01
PART /boot/efi  esp    256M      # UEFI ESP — 256M
PART /boot      ext4   1G
PART lvm        vg0    all   crypt   # 'crypt' — NIET 'encrypt'
LV vg0 swap  swap  swap  32G        # swap EERST (fixed grootte)
LV vg0 root  /     ext4  all        # root LAATSTE ('all' = rest)
IMAGE /root/images/Ubuntu-2404-noble-amd64-base.tar.gz
```

**Checklist:**
- [ ] `CRYPTPASSWORD` staat bovenaan
- [ ] Keyword is `crypt`, niet `encrypt`
- [ ] `LV ... all` staat als LAATSTE LV in de VG
- [ ] Na installatie: verwijder `/installimage.conf` van het geïnstalleerde systeem

---

## Installatieproces (stap voor stap)

### Vereisten
- Server in rescue mode (Hetzner Robot → Rescue → Linux 64-bit → Activate → Reset)
- SSH toegang: `ssh root@5.9.10.215`
- Rescue heeft ander host key dan installatiesysteem — clear met: `ssh-keygen -R 5.9.10.215`

### Stap 1: Voorbereiding in rescue

```bash
ssh root@5.9.10.215

# Stop eventuele MD arrays van eerdere installaties
mdadm --stop /dev/md0 /dev/md1 /dev/md2 2>/dev/null || true

# Beschikbare images checken
ls /root/images/
```

### Stap 2: Config aanmaken

```bash
cat > /tmp/gpu01.conf << 'EOF'
CRYPTPASSWORD VERVANG_MET_ECHTE_PASSPHRASE
DRIVE1 /dev/nvme0n1
DRIVE2 /dev/nvme1n1
SWRAID 1
SWRAIDLEVEL 1
BOOTLOADER grub
HOSTNAME gpu-01
PART /boot/efi  esp    256M
PART /boot      ext4   1G
PART lvm        vg0    all   crypt
LV vg0 swap  swap  swap  32G
LV vg0 root  /     ext4  all
IMAGE /root/images/Ubuntu-2404-noble-amd64-base.tar.gz
EOF
```

### Stap 3: Installimage draaien

```bash
# Voorgrond — zodat je direct feedback krijgt
TERM=xterm /root/.oldroot/nfs/install/installimage -a -c /tmp/gpu01.conf
```

Als er "Cancelled." verschijnt: lees `/root/debug.txt` voor de echte foutmelding.

Duurt ca. 10-15 minuten. Verwachte output bevat o.a.:
- `Found AUTOSETUP file '/autosetup'`
- `Running unattended installimage installation ...`
- Geen "Cancelled." aan het einde

### Stap 4: Na installatie — Dropbear instellen (via chroot)

Na installimage maar vóór herstart: Dropbear instellen via chroot.

```bash
# RAID assembleren en LUKS openen
mdadm --assemble --scan
cryptsetup luksOpen /dev/md1 luks-test <<< "VERVANG_MET_ECHTE_PASSPHRASE"

# LVM activeren
vgchange -ay

# Mounten
mount /dev/vg0/root /mnt/installed
mount /dev/md0 /mnt/installed/boot
mount /dev/nvme0n1p1 /mnt/installed/boot/efi

# Bind mounts voor chroot
mount --bind /proc /mnt/installed/proc
mount --bind /sys /mnt/installed/sys
mount --bind /dev /mnt/installed/dev
mount --bind /dev/pts /mnt/installed/dev/pts

# Chroot
chroot /mnt/installed /bin/bash

# --- BINNEN CHROOT ---

# Dropbear installeren
apt update && apt install -y dropbear-initramfs

# Unlock keypair aanmaken (APART van admin-sleutel!)
# Liefst op core-01, niet op de server zelf:
#   ssh-keygen -t ed25519 -f /opt/klai/gpu-unlock-key -C "klai-gpu-unlock" -N ""
# Dan public key hier plakken:
echo "ssh-ed25519 AAAA...PUBLIEKE_SLEUTEL... klai-gpu-unlock" \
  > /etc/dropbear/initramfs/authorized_keys
chmod 600 /etc/dropbear/initramfs/authorized_keys

# Dropbear opties instellen
cat > /etc/dropbear/initramfs/dropbear.conf << 'CONF'
DROPBEAR_OPTIONS="-p 2222 -s -j -k -I 120"
CONF

# Initramfs herbouwen
update-initramfs -u -k all

# Verificatie: Dropbear in initramfs?
lsinitramfs /boot/initrd.img-$(ls /boot/vmlinuz-* | tail -1 | sed 's|/boot/vmlinuz-||') | grep -E 'dropbear|cryptroot'

# CRYPTPASSWORD bestand verwijderen!
rm -f /installimage.conf

# Chroot verlaten
exit

# --- BUITEN CHROOT ---

# Config bestand verwijderen (bevat passphrase)
rm /tmp/gpu01.conf

# Opruimen
umount /mnt/installed/dev/pts
umount /mnt/installed/dev
umount /mnt/installed/sys
umount /mnt/installed/proc
umount /mnt/installed/boot/efi
umount /mnt/installed/boot
umount /mnt/installed
cryptsetup luksClose luks-test
mdadm --stop /dev/md0 /dev/md1
sleep 1 && reboot &
```

### Stap 5: Remote LUKS unlock na herstart

Na herstart wacht de server op LUKS unlock via Dropbear op port 2222.

```bash
# Wachten op Dropbear
for i in $(seq 1 30); do
  sleep 5
  nc -z -w2 5.9.10.215 2222 && echo "Dropbear bereikbaar!" && break
  echo "Wachten... $i/30"
done

# LUKS ontgrendelen — schrijf passphrase naar passfifo
# LET OP: echo -ne (geen newline, geen escaping van quotes)
ssh -p 2222 -i /opt/klai/gpu-unlock-key \
  -o StrictHostKeyChecking=accept-new \
  root@5.9.10.215 \
  "echo -ne 'LUKS_PASSPHRASE' > /lib/cryptsetup/passfifo"

# Wachten op normale boot
for i in $(seq 1 24); do
  sleep 5
  nc -z -w2 5.9.10.215 22 && echo "SSH bereikbaar op port 22!" && break
  echo "Wachten... $i/24"
done

# Host key opruimen (nieuwe installatie = nieuw host key)
ssh-keygen -R 5.9.10.215

# Verbinden
ssh -o StrictHostKeyChecking=accept-new root@5.9.10.215

# Verifieer LUKS actief
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
```

Verwachte lsblk output:
```
NAME                                             SIZE TYPE  FSTYPE
nvme0n1                                          1.7T disk
├─nvme0n1p1                                      256M part  vfat       /boot/efi
├─nvme0n1p2                                        1G part  linux_raid_member
│ └─md0                                         1022M raid1 ext4       /boot
└─nvme0n1p3                                      1.7T part  linux_raid_member
  └─md1                                          1.7T raid1 crypto_LUKS
    └─luks-XXXX                                  1.7T crypt LVM2_member
      ├─vg0-swap                                  32G lvm   swap       [SWAP]
      └─vg0-root                                 1.7T lvm   ext4       /
```

---

## Dropbear Remote Unlock — Methodes Samengevat

### Methode A: passfifo (aanbevolen — niet-interactief)

```bash
ssh -p 2222 -i /opt/klai/gpu-unlock-key root@SERVER \
  "echo -ne 'LUKS_PASSPHRASE' > /lib/cryptsetup/passfifo"
```

- Exit 0, geen output = correct
- Werkt volledig niet-interactief
- Vereist geen TTY

### Methode B: interactief (simpel — voor handmatige unlock)

```bash
ssh -t -p 2222 -i /opt/klai/gpu-unlock-key root@SERVER
# In de Dropbear shell, typ:
cryptroot-unlock
# Voer LUKS passphrase in bij de prompt
```

### Methode C: automatisch via DROPBEAR_OPTIONS (aanbevolen voor productie)

Voeg aan DROPBEAR_OPTIONS toe: `-c cryptroot-unlock`
```
DROPBEAR_OPTIONS="-p 2222 -s -j -k -I 120 -c cryptroot-unlock"
```

Dan:
```bash
ssh -t -p 2222 -i /opt/klai/gpu-unlock-key root@SERVER
# Dropbear loopt direct cryptroot-unlock — voer passphrase in
```

### Wat NIET werkt

```bash
# FOUT: cryptroot-unlock leest van /dev/console, niet stdin
echo "passphrase" | ssh ... cryptroot-unlock    # ← werkt niet
ssh ... "cryptroot-unlock" <<< "passphrase"     # ← werkt niet
```

---

## Post-installatie Security Checklist

- [x] `/installimage.conf` verwijderd van geïnstalleerd systeem (bevat LUKS passphrase)
- [ ] LUKS passphrase opgeslagen in team password manager onder "gpu-01 LUKS"
- [x] LUKS passphrase NIET in git, SOPS, of server-bestanden
- [x] Dropbear actief op poort 2222 met dedicated unlock-sleutel
- [x] Remote unlock getest (reboot → Dropbear → passfifo → ssh port 22)
- [ ] `PasswordAuthentication no` in `/etc/ssh/sshd_config` verificeren
- [x] Unlocksleutel is ANDERS dan admin SSH-sleutel

---

## Bronnen

- [Hetzner: installimage documentatie](https://docs.hetzner.com/robot/dedicated-server/operating-systems/installimage/)
- [Hetzner Community: Debian + LVM + Encrypted NVMe RAID](https://community.hetzner.com/tutorials/install-debian-with-lvm-encrypted-nvme-software-raid/)
- [GitHub: disk-encryption-hetzner (TheReal1604)](https://github.com/TheReal1604/disk-encryption-hetzner/blob/master/ubuntu/ubuntu_swraid_lvm_luks.md) — Hetzner-specifieke guide
- [Ubuntu 24.04 Dropbear Setup (originell.org)](https://www.originell.org/til/ubuntu-24-dropbear-setup/) — Ubuntu 24.04 specifiek
- [nixCraft: How to unlock LUKS using Dropbear SSH](https://www.cyberciti.biz/security/how-to-unlock-luks-using-dropbear-ssh-keys-remotely-in-linux/)
- [hamy.io: Remote unlocking of LUKS in Ubuntu/Debian](https://hamy.io/post/0005/remote-unlocking-of-luks-encrypted-root-in-ubuntu-debian/)
- [GitHub: hetzneronline/installimage - functions.sh](https://github.com/hetzneronline/installimage/blob/master/functions.sh)
