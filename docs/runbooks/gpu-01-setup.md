# gpu-01 Setup Runbook — LUKS + Docker + SSH Tunnels

**Server:** Hetzner GEX44 #2963286 | IP: 5.9.10.215 | FSN1-DC13 (Germany)
**GPU:** RTX 4000 SFF Ada Generation (20 GB GDDR6)
**Disks:** 2× 1.7 TB NVMe (RAID1 via mdadm)
**OS target:** Ubuntu 24.04 LTS + LUKS full-disk encryption

**SPEC:** SPEC-GPU-001 v2.0

---

## Sessielog / Wat Geleerd

### 2026-03-29 — Eerste installimage pogingen

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

**Bewijs:** [Hetzner installimage source - functions.sh](https://github.com/hetzneronline/installimage/blob/master/functions.sh) — `PART_CRYPT[$i]` wordt ingesteld op het 5e veld van de PART-regel. Validatie checkt specifiek op `crypt` (zie ESP-check: `grep 'crypt'`).

#### Fout 2: Verwarring exit code 0

De eerste poging draaide met `nohup ... &` — SSH-commando exited 0 (de achtergrondstart slaagde), maar het eigenlijke `installimage` process exited 1 ("Cancelled."). De context summary noemde dit foutief "exit code 0 voor de installatie".

#### Bevinding 3: Hetzner `/autosetup` mechanisme

Wanneer installimage draait met `-c /tmp/config.conf`, kopieert het intern de config naar `/autosetup` en draait autosetup.sh. Vandaar het "Found AUTOSETUP file '/autosetup'" bericht — dit is **normaal gedrag**, zelfs als je `-c` gebruikt.

#### Bevinding 4: Fabrieksinstallatie op de disks

De partitie-layout die we zagen (nvme0n1p1: 256M vfat, RAID1 arrays) was van de **originele Hetzner fabrieksinstallatie** van Ubuntu 24.04. Onze installimage-pogingen faalden allemaal, maar de fabrieksinstallatie bleef op de disks staan.

#### Bevinding 5: CRYPTPASSWORD in /installimage.conf

Na een succesvolle LUKS-installatie plaatst installimage een kopie van de config (inclusief CRYPTPASSWORD) in `/installimage.conf` op het geïnstalleerde systeem. **Dit bestand moet na installatie worden verwijderd.**

---

## Correcte installimage Config

```bash
# Bestand: /tmp/gpu01.conf
CRYPTPASSWORD <passphrase>       # BOVENAAN — niet onderaan
DRIVE1 /dev/nvme0n1
DRIVE2 /dev/nvme1n1
SWRAID 1
SWRAIDLEVEL 1
BOOTLOADER grub
HOSTNAME gpu-01
PART /boot/efi  esp    256M      # UEFI ESP — 256M (niet 512M)
PART /boot      ext4   1G
PART lvm        vg0    all   crypt   # 'crypt' — NIET 'encrypt'
LV vg0 root  /     ext4  all
LV vg0 swap  swap  swap  32G
IMAGE /root/images/Ubuntu-2404-noble-amd64-base.tar.gz
```

**Let op:**
- `CRYPTPASSWORD` staat bovenaan (vóór DRIVE definities)
- Keyword is `crypt`, niet `encrypt`
- UEFI ESP: 256M (officieel Hetzner voorbeeld gebruikt 256M)
- LV swap syntax: `LV vg0 swap  swap  swap  32G` (naam=swap, mountpoint=swap, fs=swap) — dit is correct
- Na installatie: verwijder `/installimage.conf` van het geïnstalleerde systeem

---

## Installatieproces (stap voor stap)

### Vereisten
- Server in rescue mode (Hetzner Robot → Rescue → Linux 64-bit → Activate → Reset)
- SSH toegang: `ssh root@5.9.10.215` (rescue heeft ander host key — clear met `ssh-keygen -R 5.9.10.215`)

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
LV vg0 root  /     ext4  all
LV vg0 swap  swap  swap  32G
IMAGE /root/images/Ubuntu-2404-noble-amd64-base.tar.gz
EOF
```

### Stap 3: Installimage draaien

```bash
# TERM moet gezet zijn — installimage gebruikt dialog voor foutmeldingen
TERM=xterm /root/.oldroot/nfs/install/installimage -a -c /tmp/gpu01.conf
```

Verwachte output als het goed gaat:
```
Found AUTOSETUP file '/autosetup'
Running unattended installimage installation ...
[config echo]
...
# Geen "Cancelled." — maar echte installatie output
```

Duurt ca. 5-10 minuten.

### Stap 4: Na installatie — opruimen

```bash
# Config verwijderen van rescue (bevat passphrase)
rm /tmp/gpu01.conf

# Server herstarten
reboot
```

### Stap 5: Dropbear instellen voor remote LUKS unlock

Na de herstart is de server in LUKS-vergrendeld staat. SSH via Dropbear op poort 2222 is NIET automatisch actief na een verse installimage — dat moet handmatig worden ingesteld.

**Aanpak:** Booten in de geïnstalleerde Ubuntu (vereist console of opnieuw rescue + chroot), dan:

```bash
apt install dropbear-initramfs

# Dropbear configureren
echo 'DROPBEAR_OPTIONS="-p 2222 -s"' >> /etc/dropbear/initramfs/dropbear.conf

# Unlock-sleutel aanmaken (APART van admin-sleutel)
ssh-keygen -t ed25519 -f ~/.ssh/gpu-unlock-key -C "klai-gpu-unlock" -N ""

# Publieke sleutel toevoegen aan initramfs
mkdir -p /etc/dropbear/initramfs
cat ~/.ssh/gpu-unlock-key.pub > /etc/dropbear/initramfs/authorized_keys
chmod 600 /etc/dropbear/initramfs/authorized_keys

# Initramfs herbouwen
update-initramfs -u

# Controleer
lsinitramfs /boot/initrd.img-$(uname -r) | grep dropbear
```

### Stap 6: LUKS unlock testen

```bash
# Na herstart — server wacht op unlock
ssh -p 2222 -i ~/.ssh/gpu-unlock-key root@5.9.10.215

# In Dropbear shell
cryptroot-unlock   # Voer LUKS passphrase in

# Server start door — wacht ca. 2 minuten
ssh gpu-01   # Normale SSH login
```

---

## Alternatieve Aanpak: Handmatige LUKS (zonder installimage CRYPTPASSWORD)

Als installimage-encryptie problemen blijft geven, is de handmatige aanpak betrouwbaarder:

1. **Installeer zonder encryptie** via installimage (zonder `crypt` en `CRYPTPASSWORD`)
2. **Reboot naar rescue** (geen reboot naar geïnstalleerd systeem)
3. **Assembleer RAID:** `mdadm --assemble --scan`
4. **Zet LUKS op md2 (de grote RAID):**
   ```bash
   cryptsetup --cipher aes-xts-plain64 --key-size 256 --hash sha256 \
     --iter-time 6000 --batch-mode luksFormat /dev/md2 <<< "PASSPHRASE"
   ```
5. **Open LUKS, maak LVM:**
   ```bash
   cryptsetup luksOpen /dev/md2 cryptroot <<< "PASSPHRASE"
   pvcreate /dev/mapper/cryptroot
   vgcreate vg0 /dev/mapper/cryptroot
   lvcreate -L 32G -n swap vg0
   lvcreate -l 100%FREE -n root vg0
   ```
6. **Formatteer en mount:**
   ```bash
   mkfs.ext4 /dev/vg0/root
   mkswap /dev/vg0/swap
   mount /dev/vg0/root /mnt
   ```
7. **Chroot en herstel OS** (kopieer van tijdelijke installatie of tar.gz)
8. **Update crypttab, update-initramfs, update-grub**

---

## Post-installatie Security Checklist

- [ ] `/installimage.conf` verwijderd van geïnstalleerd systeem (bevat LUKS passphrase)
- [ ] LUKS passphrase opgeslagen in team password manager onder "gpu-01 LUKS"
- [ ] LUKS passphrase NIET in git, SOPS, of server-bestanden
- [ ] Dropbear actief op poort 2222 met dedicated unlock-sleutel
- [ ] Remote unlock getest (reboot → Dropbear → cryptroot-unlock → ssh gpu-01)
- [ ] `PasswordAuthentication no` in `/etc/ssh/sshd_config`
- [ ] Unlocksleutel is ANDERS dan admin SSH-sleutel

---

## Bronnen

- [Hetzner: installimage documentatie](https://docs.hetzner.com/robot/dedicated-server/operating-systems/installimage/)
- [Hetzner Community: Debian + LVM + Encrypted NVMe RAID](https://community.hetzner.com/tutorials/install-debian-with-lvm-encrypted-nvme-software-raid/)
- [GitHub: disk-encryption-hetzner (TheReal1604)](https://github.com/TheReal1604/disk-encryption-hetzner/blob/master/ubuntu/ubuntu_swraid_lvm_luks.md)
- [GitHub: hetzneronline/installimage - functions.sh](https://github.com/hetzneronline/installimage/blob/master/functions.sh)
