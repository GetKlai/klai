# gpu-01 Rollout Runbook

**Server:** Hetzner GEX44 #2963286 | IP: 5.9.10.215 | FSN1-DC13 (Germany)
**GPU:** RTX 4000 SFF Ada Generation (20 GB GDDR6)
**Disks:** 2× 1.7 TB NVMe (RAID1 via mdadm)
**OS:** Ubuntu 24.04 LTS + LUKS full-disk encryption

**SPEC:** SPEC-GPU-001 v2.0

---

## Status

| Phase | Status | Datum |
|---|---|---|
| Phase 0: LUKS OS installatie | ✅ Gereed | 2026-03-30 |
| Phase 1: Docker + GPU services | ✅ Gereed | 2026-03-30 |
| Phase 2: SSH tunnels op core-01 | ✅ Gereed | 2026-03-30 |
| Phase 3: Core-01 consumer migratie | ✅ Gereed | 2026-03-30 |
| Phase 4: Monitoring | ✅ Gereed | 2026-03-30 |

---

## Rollout — Stap voor Stap

> Gebruik dit gedeelte bij een nieuwe rollout of volledige herinstallatie.
> De secties hieronder zijn in volgorde — voer ze volledig af vóór je naar de volgende gaat.

### Vereisten (verzamel dit vóór je begint)

| Wat | Waar te vinden | Opmerking |
|---|---|---|
| LUKS passphrase (nieuw) | Genereer: `openssl rand -base64 32` | Sla op in team password manager onder "gpu-01 LUKS" |
| Dropbear unlock-sleutel (privaat) | `core-01:/opt/klai/gpu-unlock-key` | ANDERE sleutel dan admin — aanmaken als afwezig |
| Tunnel-sleutel (privaat) | `core-01:/opt/klai/gpu-tunnel-key` | Aanmaken als afwezig (zie Phase 2) |
| GHCR credentials | `core-01:~/.docker/config.json` | `ghcr.io/getklai/*` images zijn private |
| SSH toegang tot core-01 | `ssh core-01` | Via SSH config alias |
| SSH toegang tot public-01 | `ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64` | Voor Uptime Kuma |

**Dropbear unlock-sleutel aanmaken (als afwezig):**
```bash
ssh core-01 "
  if [ ! -f /opt/klai/gpu-unlock-key ]; then
    ssh-keygen -t ed25519 -f /opt/klai/gpu-unlock-key -C 'klai-gpu-unlock' -N ''
    echo 'Aangemaakt:'
    cat /opt/klai/gpu-unlock-key.pub
  else
    echo 'Bestaat al:'
    cat /opt/klai/gpu-unlock-key.pub
  fi
"
```

---

### Phase 0: OS Installatie

**Doel:** Ubuntu 24.04 + LUKS encryptie + RAID1 installeren via Hetzner installimage.

#### Stap 0.1 — Server in rescue mode zetten

Hetzner Robot → Server → Rescue → Linux 64-bit → Activate → Reset.

```bash
# Wacht ca. 2 min, dan:
ssh-keygen -R 5.9.10.215   # rescue heeft ander host key
ssh root@5.9.10.215
```

#### Stap 0.2 — installimage config aanmaken

```bash
# Stop eventuele MD arrays van vorige installaties
mdadm --stop /dev/md0 /dev/md1 /dev/md2 2>/dev/null || true

# Maak config aan
# Vervang VERVANG_MET_ECHTE_PASSPHRASE met de gegenereerde LUKS passphrase
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

> **Kritieke regels:**
> - Keyword is `crypt` — NIET `encrypt` (installimage valideert dit streng)
> - `LV ... all` moet de LAATSTE LV in de VG zijn
> - `CRYPTPASSWORD` staat BOVENAAN het bestand

#### Stap 0.3 — installimage draaien

```bash
TERM=xterm /root/.oldroot/nfs/install/installimage -a -c /tmp/gpu01.conf
```

Duurt ca. 10-15 minuten. Als "Cancelled." verschijnt: lees `/root/debug.txt` voor de echte foutmelding. Herstart de stap pas als je de oorzaak hebt.

#### Stap 0.4 — Dropbear instellen via chroot

Na installimage maar **vóór de herstart**:

```bash
# RAID assembleren en LUKS openen
mdadm --assemble --scan
cryptsetup luksOpen /dev/md1 luks-install <<< "VERVANG_MET_ECHTE_PASSPHRASE"
vgchange -ay

# Mounten
mount /dev/vg0/root /mnt/installed
mount /dev/md0      /mnt/installed/boot
mount /dev/nvme0n1p1 /mnt/installed/boot/efi
for d in proc sys dev dev/pts; do mount --bind /$d /mnt/installed/$d; done

# Chroot
chroot /mnt/installed /bin/bash

# --- BINNEN CHROOT ---

# Dropbear installeren
apt update && apt install -y dropbear-initramfs

# Public key van core-01 ophalen en installeren
# (Voer dit commando UIT de chroot uit, kopier de output, plak het terug)
# Extern: ssh core-01 "cat /opt/klai/gpu-unlock-key.pub"
PUBKEY="ssh-ed25519 AAAA...PASTE_OUTPUT_HERE..."
echo "$PUBKEY" > /etc/dropbear/initramfs/authorized_keys
chmod 600 /etc/dropbear/initramfs/authorized_keys

# Dropbear opties
cat > /etc/dropbear/initramfs/dropbear.conf << 'CONF'
DROPBEAR_OPTIONS="-p 2222 -s -j -k -I 120"
CONF

# Initramfs herbouwen
update-initramfs -u -k all

# Verifieer Dropbear in initramfs
lsinitramfs /boot/initrd.img-$(ls /boot/vmlinuz-* | tail -1 | sed 's|/boot/vmlinuz-||') \
  | grep -E 'dropbear|cryptroot'

# VERWIJDER de installimage config (bevat LUKS passphrase in plaintext)
rm -f /installimage.conf

exit   # chroot verlaten
```

```bash
# --- BUITEN CHROOT --- config verwijderen en opruimen
rm /tmp/gpu01.conf

umount /mnt/installed/dev/pts
umount /mnt/installed/dev
umount /mnt/installed/sys
umount /mnt/installed/proc
umount /mnt/installed/boot/efi
umount /mnt/installed/boot
umount /mnt/installed
cryptsetup luksClose luks-install
mdadm --stop /dev/md0 /dev/md1

sleep 1 && reboot &
```

#### Stap 0.5 — Remote LUKS unlock na herstart

```bash
# Wachten op Dropbear (port 2222)
for i in $(seq 1 30); do
  sleep 5
  nc -z -w2 5.9.10.215 2222 && echo "Dropbear bereikbaar!" && break
  echo "Wachten... $i/30"
done

# LUKS ontgrendelen via passfifo (niet-interactief)
ssh -p 2222 -i /opt/klai/gpu-unlock-key \
  -o StrictHostKeyChecking=accept-new \
  root@5.9.10.215 \
  "echo -ne 'LUKS_PASSPHRASE' > /lib/cryptsetup/passfifo"
# Exit 0 zonder output = correct. Server begint nu te booten.

# Wachten op normale SSH (port 22)
for i in $(seq 1 24); do
  sleep 5
  nc -z -w2 5.9.10.215 22 && echo "SSH bereikbaar op port 22!" && break
  echo "Wachten... $i/24"
done

# Host key vernieuwen (nieuwe installatie = nieuw key)
ssh-keygen -R 5.9.10.215
ssh -o StrictHostKeyChecking=accept-new root@5.9.10.215

# Verifieer LUKS actief
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
```

#### Verificatie Phase 0

```bash
# Verwachte lsblk output:
# nvme0n1p3 → md1 (raid1) → luks-XXXX (crypt) → vg0-root (lvm) / vg0-swap
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT | grep -E 'crypt|raid|lvm'

# OS versie
cat /etc/os-release | grep PRETTY_NAME
# Verwacht: Ubuntu 24.04.x LTS
```

---

### Phase 0.5: Security Hardening

**Doel:** Firewall, brute-force bescherming, SSH hardening, en automatische security updates.

> Voer dit uit **direct na Phase 0**, terwijl je nog op gpu-01 bent ingelogd.

#### Stap 0.5.1 — SSH hardening

```bash
# Controleer huidige staat
grep -E '^(PasswordAuthentication|PermitRootLogin)' /etc/ssh/sshd_config

# Fix als nodig (Hetzner installimage zet dit normaal al goed)
sed -i 's/#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload ssh

# Verifieer
sshd -T | grep -E 'passwordauthentication|permitrootlogin'
# Verwacht: passwordauthentication no / permitrootlogin without-password
```

#### Stap 0.5.2 — UFW firewall

```bash
# Installeer en configureer
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
echo 'y' | ufw enable

# Verifieer
ufw status verbose
# Verwacht: Status active, deny incoming, allow 22/tcp
```

> Docker services (7997, 8001, 8000) binden aan `127.0.0.1` — UFW hoeft hier geen regels voor.
> Dropbear (port 2222) draait in initramfs vóór UFW geladen wordt — UFW blokkeert dit niet.

#### Stap 0.5.3 — fail2ban

```bash
DEBIAN_FRONTEND=noninteractive apt-get install -y fail2ban

cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
backend  = systemd

[sshd]
enabled = true
port    = ssh
filter  = sshd
EOF

systemctl enable fail2ban
systemctl start fail2ban

# Verifieer
fail2ban-client status sshd
# Verwacht: jail sshd actief, mogelijk al bans
```

#### Stap 0.5.4 — Automatische security updates

```bash
# Controleer of unattended-upgrades actief is (Hetzner installeert dit standaard)
dpkg -l unattended-upgrades | grep -q ii && echo 'OK: installed' || apt-get install -y unattended-upgrades
systemctl is-active unattended-upgrades
# Verwacht: active
```

#### Verificatie Phase 0.5

```bash
# Alles in één check
echo "=== UFW ===" && ufw status | head -5
echo "=== fail2ban ===" && fail2ban-client status sshd | grep -E 'Currently|Total'
echo "=== SSH ===" && sshd -T | grep -E 'passwordauth|permitroot'
echo "=== Updates ===" && systemctl is-active unattended-upgrades
```

---

### Phase 1: Docker + GPU Services

**Doel:** NVIDIA driver, Docker CE, GPU services draaien op gpu-01.

#### Stap 1.1 — NVIDIA driver + Docker CE

```bash
ssh root@5.9.10.215

# NVIDIA driver (geen reboot nodig — DKMS bouwt on-the-fly)
apt install -y ubuntu-drivers-common linux-headers-$(uname -r)
DEBIAN_FRONTEND=noninteractive apt install -y nvidia-driver-570
nvidia-smi   # verifieer direct

# Docker CE
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker

# NVIDIA Container Toolkit
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update -q && apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

# Test GPU in Docker
docker run --rm --gpus all ubuntu:22.04 nvidia-smi
```

#### Stap 1.2 — GHCR login

```bash
# Token ophalen van core-01 en inloggen
GHCR_TOKEN=$(ssh core-01 "cat ~/.docker/config.json" \
  | python3 -c "import json,sys,base64; c=json.load(sys.stdin); \
    a=c['auths']['ghcr.io']['auth']; print(base64.b64decode(a).decode().split(':')[1])")

echo "$GHCR_TOKEN" | docker login ghcr.io -u getklai --password-stdin
```

#### Stap 1.3 — Services deployen

```bash
# Service bestanden structuur aanmaken
mkdir -p /opt/klai-gpu

# docker-compose.yml kopiëren vanuit repo (via core-01)
scp core-01:/opt/klai-gpu/docker-compose.yml /opt/klai-gpu/docker-compose.yml
# Of: scp local:deploy/gpu-01/docker-compose.yml root@5.9.10.215:/opt/klai-gpu/

# BGE-M3 sparse Python service kopiëren (CPU-based sparse embeddings)
mkdir -p /opt/klai-gpu/bge-m3-sparse
scp core-01:/opt/klai/bge-m3-sparse/main.py /opt/klai-gpu/bge-m3-sparse/main.py
# Dockerfile is in de repo: deploy/gpu-01/bge-m3-sparse/Dockerfile

# Services starten
cd /opt/klai-gpu
docker compose up -d
# Wacht ~2 min op model downloads + GPU warmup
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

#### Verificatie Phase 1

```bash
# Health checks vanuit gpu-01
curl -sf http://127.0.0.1:7997/health   # Infinity (embeddings + reranker)
curl -sf http://127.0.0.1:8001/health   # BGE-M3 sparse
curl -sf http://127.0.0.1:8000/health   # Whisper

# Embedding test
curl -s -X POST http://127.0.0.1:7997/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"BAAI/bge-m3","input":"test"}' \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print(len(r['data'][0]['embedding']), 'dims')"
# Verwacht: 1024 dims

# VRAM gebruik
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits
# Verwacht: ~5400 van 20475 MB (~27%)
```

---

### Phase 2: SSH Tunnels (core-01)

**Doel:** autossh tunnel van core-01 naar gpu-01 zodat Docker containers op klai-net de GPU services bereiken via 172.18.0.1.

#### Stap 2.1 — autossh + tunnel keypair

```bash
# autossh installeren op core-01
ssh core-01 "sudo apt-get install -y autossh"

# Tunnel keypair aanmaken als afwezig
ssh core-01 "
  if [ ! -f /opt/klai/gpu-tunnel-key ]; then
    ssh-keygen -t ed25519 -f /opt/klai/gpu-tunnel-key -C 'klai-gpu-tunnel@core-01' -N ''
    chmod 600 /opt/klai/gpu-tunnel-key
    echo 'Aangemaakt — public key:'
    cat /opt/klai/gpu-tunnel-key.pub
  else
    echo 'Bestaat al:'
    cat /opt/klai/gpu-tunnel-key.pub
  fi
"
```

#### Stap 2.2 — Public key installeren op gpu-01

```bash
PUBKEY=$(ssh core-01 "cat /opt/klai/gpu-tunnel-key.pub")
ssh root@5.9.10.215 "echo '$PUBKEY' >> ~/.ssh/authorized_keys"

# Verifieer connectie
ssh core-01 "ssh -i /opt/klai/gpu-tunnel-key \
  -o StrictHostKeyChecking=accept-new \
  root@5.9.10.215 'echo OK && hostname'"
```

#### Stap 2.3 — systemd service aanmaken

> **Netwerkbinding:** Tunnel bindt aan `172.18.0.1` (klai-net Docker bridge gateway).
> Docker containers kunnen de GPU services via dit IP bereiken. Van buiten is het niet routeerbaar.

```bash
ssh core-01 "sudo tee /etc/systemd/system/gpu-tunnel.service > /dev/null << 'EOF'
[Unit]
Description=SSH Tunnel to gpu-01 (GPU inference services)
After=network-online.target
Wants=network-online.target

[Service]
User=klai
Environment=AUTOSSH_GATETIME=0
ExecStart=/usr/bin/autossh -M 0 \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -o StrictHostKeyChecking=yes \
  -i /opt/klai/gpu-tunnel-key \
  -N \
  -L 172.18.0.1:7997:127.0.0.1:7997 \
  -L 172.18.0.1:8001:127.0.0.1:8001 \
  -L 172.18.0.1:8000:127.0.0.1:8000 \
  root@5.9.10.215
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
"
```

> **Veiligheid:** `StrictHostKeyChecking=yes` voorkomt MitM-aanvallen op de tunnel.
> Na de eerste verbinding slaat SSH het host key op in `~/.ssh/known_hosts`.
> Als de server opnieuw wordt geïnstalleerd, verwijder het oude key:
> `ssh core-01 "ssh-keygen -R 5.9.10.215"`

#### Stap 2.4 — Service activeren

```bash
ssh core-01 "
sudo systemctl daemon-reload
sudo systemctl enable gpu-tunnel.service
sudo systemctl start gpu-tunnel.service
sleep 3
sudo systemctl status gpu-tunnel.service --no-pager
"
```

#### Verificatie Phase 2

```bash
ssh core-01 "
# Tunnel ports gebonden op 172.18.0.1?
sudo ss -tlnp | grep 172.18.0.1

# Alle drie services bereikbaar?
curl -sf http://172.18.0.1:7997/health   # Infinity
curl -sf http://172.18.0.1:8001/health   # BGE-M3 sparse
curl -sf http://172.18.0.1:8000/health   # Whisper
"
```

---

### Phase 3: Core-01 Consumer Migratie

**Doel:** core-01 consumers laten communiceren met de GPU services via de tunnel (172.18.0.1) in plaats van via de oude CPU containers.

#### URL mapping

| Variabele | Oud | Nieuw |
|---|---|---|
| `TEI_URL` | `http://tei:8080` | `http://172.18.0.1:7997` |
| `TEI_RERANKER_URL` | `http://infinity-reranker:7997` | `http://172.18.0.1:7997` |
| `SPARSE_SIDECAR_URL` | `http://bge-m3-sparse:8001` | `http://172.18.0.1:8001` |
| `WHISPER_SERVER_URL` | `http://whisper-server:8000` | `http://172.18.0.1:8000` |
| `TRANSCRIBER_URL` | `http://whisper-server:8000/...` | `http://172.18.0.1:8000/...` |
| `JINA_API_URL` | `http://infinity-reranker:7997/v1/rerank` | `http://172.18.0.1:7997/v1/rerank` |

#### Stap 3.1 — Consumer code deployen

De volgende bestanden zijn al bijgewerkt voor de Infinity OpenAI API (`/v1/embeddings`) in plaats van de TEI API (`/embed`):

- `klai-retrieval-api/retrieval_api/services/tei.py`
- `klai-knowledge-ingest/knowledge_ingest/embedder.py`
- `klai-focus/research-api/app/services/tei.py`

Build en push de images via de normale CI workflows.

#### Stap 3.2 — docker-compose.yml bijwerken op core-01

```bash
# Backup nemen
ssh core-01 "cp /opt/klai/docker-compose.yml /opt/klai/docker-compose.yml.bak-$(date +%s)"

# Sync vanuit repo
scp deploy/docker-compose.yml core-01:/opt/klai/docker-compose.yml
```

De compose file heeft de oude GPU services op `profiles: [gpu-disabled]` staan (ze draaien niet meer).
De URL env vars zijn bijgewerkt naar 172.18.0.1.

#### Stap 3.3 — Oude GPU services stoppen + consumers herstarten

```bash
ssh core-01 "
cd /opt/klai

# Oude CPU GPU containers verwijderen (staan al op gpu-disabled, maar voor zekerheid)
docker compose stop tei bge-m3-sparse whisper-server infinity-reranker 2>/dev/null || true
docker compose rm -f tei bge-m3-sparse whisper-server infinity-reranker 2>/dev/null || true

# Consumer services herstarten met nieuwe config
docker compose up -d retrieval-api knowledge-ingest scribe-api vexa-bot-manager librechat-klai
"
```

#### Verificatie Phase 3

```bash
ssh core-01 "
# Containers draaien?
docker ps --format 'table {{.Names}}\t{{.Status}}' \
  | grep -E 'retrieval|knowledge|scribe|vexa|librechat'

# Logs zonder fouten?
docker logs klai-core-retrieval-api-1 2>&1 | tail -5
docker logs klai-core-knowledge-ingest-1 2>&1 | tail -5

# Embedding test vanuit klai-net container
docker run --rm --network klai-net curlimages/curl:latest \
  -sf -X POST http://172.18.0.1:7997/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{\"input\": \"test\", \"model\": \"BAAI/bge-m3\"}' \
  -o /dev/null -w 'HTTP %{http_code}\n'
# Verwacht: HTTP 200
"
```

---

### Phase 4: Monitoring

**Doel:** Uptime Kuma push monitor voor alle drie GPU services via een gecombineerde health check.

#### Stap 4.1 — Push token genereren

```bash
TOKEN=$(ssh core-01 "openssl rand -hex 16")
echo "Token: $TOKEN"  # bewaar dit voor stap 4.2 en 4.4
```

#### Stap 4.2 — Uptime Kuma monitor aanmaken

```bash
CONTAINER=uptime-kuma-ucowwogo0ogoskwk0ggg4o48
TOKEN="<token uit stap 4.1>"
MONITOR_NAME="GPU Services (gpu-01)"

ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64 \
  "docker cp ${CONTAINER}:/app/data/kuma.db /tmp/kuma.db && python3 -c \"
import sqlite3, json
db = sqlite3.connect('/tmp/kuma.db')
db.execute('''INSERT INTO monitor (name, type, push_token, active, user_id, interval, maxretries, upside_down, accepted_statuscodes_json, retry_interval, resend_interval)
              VALUES (?, 'push', ?, 1, 1, 60, 0, 0, ?, 20, 0)''',
           ('${MONITOR_NAME}', '${TOKEN}', json.dumps(['200-299'])))
db.commit()
print('inserted id:', db.execute('SELECT last_insert_rowid()').fetchone()[0])
db.close()
\" && docker cp /tmp/kuma.db ${CONTAINER}:/app/data/kuma.db && rm /tmp/kuma.db"

# Uptime Kuma herstarten (laadt monitors bij opstart)
ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64 "docker restart ${CONTAINER}"
```

#### Stap 4.3 — Scripts deployen naar core-01

```bash
# Scripts zijn in de klai-infra repo
scp klai-infra/core-01/scripts/gpu-health.sh core-01:/opt/klai/scripts/gpu-health.sh
scp klai-infra/core-01/scripts/push-health.sh core-01:/opt/klai/scripts/push-health.sh
ssh core-01 "chmod +x /opt/klai/scripts/gpu-health.sh /opt/klai/scripts/push-health.sh"
```

#### Stap 4.4 — Token toevoegen aan SOPS env

```bash
cd /tmp
SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt \
  sops --decrypt --input-type dotenv --output-type dotenv \
  ~/Server/projects/klai/klai-infra/core-01/.env.sops > klai-env-plain

echo "KUMA_TOKEN_GPU_SERVICES=<token uit stap 4.1>" >> klai-env-plain

SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt \
  sops --encrypt --input-type dotenv --output-type dotenv \
  --age age1lyd243tsj8j7rn2wy4hdmnya99wsf2p87fpphys9k65kammerqsqnzpsur,age15ztzw9vnngkdnw0pg5tn8upplglvhzkep23sm5zu86res5lcmv7syw5m4v \
  klai-env-plain > ~/Server/projects/klai/klai-infra/core-01/.env.sops

rm klai-env-plain

# Deploy (synct .env naar core-01)
cd ~/Server/projects/klai/klai-infra/core-01 && bash deploy.sh main
```

#### Verificatie Phase 4

```bash
# GPU health script handmatig testen
ssh core-01 "bash /opt/klai/scripts/gpu-health.sh && echo OK || echo FAIL"

# Volledige push-health draaien
ssh core-01 "bash /opt/klai/scripts/push-health.sh"

# Uptime Kuma: laatste heartbeats GPU monitor
CONTAINER=uptime-kuma-ucowwogo0ogoskwk0ggg4o48
ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64 \
  "docker exec ${CONTAINER} sh -c 'sqlite3 /app/data/kuma.db \
    \"SELECT h.status, h.msg, h.time FROM heartbeat h \
      JOIN monitor m ON m.id=h.monitor_id \
      WHERE m.name=\\\"GPU Services (gpu-01)\\\" \
      ORDER BY h.time DESC LIMIT 5\"'"
# Status 1 = up, 0 = down
```

---

## Rollback Procedure

Als de migratie ongedaan gemaakt moet worden (terugvallen op core-01 CPU containers):

```bash
# Stap 1: Stop tunnel
ssh core-01 "sudo systemctl stop gpu-tunnel.service"

# Stap 2: Herstel backup compose
BACKUP=$(ssh core-01 "ls -t /opt/klai/docker-compose.yml.bak-* | head -1")
ssh core-01 "cp $BACKUP /opt/klai/docker-compose.yml"

# Stap 3: Start oude GPU services opnieuw
ssh core-01 "cd /opt/klai && docker compose --profile gpu-disabled up -d \
  tei bge-m3-sparse whisper-server infinity-reranker"

# Stap 4: Herstart consumers
ssh core-01 "cd /opt/klai && docker compose up -d \
  retrieval-api knowledge-ingest scribe-api vexa-bot-manager"
```

Rollback tijd: ~5-10 minuten (model warmup 2-3 min).

---

## Operationele Procedures

### Remote LUKS Unlock na reboot

```bash
# Stap 1: Wachten op Dropbear (port 2222)
for i in $(seq 1 30); do
  sleep 5
  nc -z -w2 5.9.10.215 2222 && echo "Dropbear bereikbaar!" && break
  echo "Wachten... $i/30"
done

# Stap 2: Unlock via passfifo
ssh -p 2222 -i /opt/klai/gpu-unlock-key \
  -o StrictHostKeyChecking=yes \
  root@5.9.10.215 \
  "echo -ne 'LUKS_PASSPHRASE' > /lib/cryptsetup/passfifo"
# Exit 0 zonder output = correct

# Stap 3: Wachten op normale SSH
for i in $(seq 1 24); do
  sleep 5
  nc -z -w2 5.9.10.215 22 && echo "SSH bereikbaar!" && break
  echo "Wachten... $i/24"
done
```

> **Let op:** `echo -ne` is vereist — `-n` voorkomt trailing newline, `-e` verwerkt escape sequences.
> `cryptroot-unlock` leest van `/dev/console`, NIET van stdin — dus pipen werkt niet.

### GPU Services herstarten (onderhoud / update)

```bash
# Op gpu-01
ssh root@5.9.10.215 "cd /opt/klai-gpu && docker compose restart"

# Wacht op warmup (~2 min), dan verificeer
ssh root@5.9.10.215 "
  curl -sf http://127.0.0.1:7997/health
  curl -sf http://127.0.0.1:8001/health
  curl -sf http://127.0.0.1:8000/health
"
```

### GPU Tunnel herstarten

```bash
ssh core-01 "sudo systemctl restart gpu-tunnel.service && sleep 3 && \
  sudo systemctl status gpu-tunnel.service --no-pager"
```

### Monitoring handmatig testen

```bash
# GPU health check
ssh core-01 "bash /opt/klai/scripts/gpu-health.sh && echo OK || echo FAIL"

# Check logs
ssh core-01 "tail -20 /opt/klai/logs/health.log"

# Recente Uptime Kuma heartbeats
CONTAINER=uptime-kuma-ucowwogo0ogoskwk0ggg4o48
ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64 \
  "docker exec ${CONTAINER} sh -c 'sqlite3 /app/data/kuma.db \
    \"SELECT h.status, h.msg, h.time FROM heartbeat h \
      JOIN monitor m ON m.id=h.monitor_id \
      WHERE m.name=\\\"GPU Services (gpu-01)\\\" \
      ORDER BY h.time DESC LIMIT 5\"'"
```

---

## Security Checklist (na elke installatie)

### Secrets
- [x] `/installimage.conf` verwijderd (bevat LUKS passphrase in plaintext)
- [ ] LUKS passphrase opgeslagen in team password manager onder "gpu-01 LUKS"
- [x] LUKS passphrase NIET in git, SOPS, of server-bestanden

### SSH & toegang
- [x] `PasswordAuthentication no` in `/etc/ssh/sshd_config`
- [x] `PermitRootLogin prohibit-password` (alleen key-based)
- [x] Dropbear actief op poort 2222 met dedicated unlock-sleutel
- [x] Unlock-sleutel is ANDERS dan admin SSH-sleutel
- [x] Tunnel-sleutel is ANDERS dan admin SSH-sleutel
- [x] `StrictHostKeyChecking=yes` in `gpu-tunnel.service` (geen MitM risico)
- [x] Remote unlock getest (reboot → Dropbear → passfifo → SSH port 22)

### Netwerk & bescherming
- [x] UFW actief: deny incoming, allow 22/tcp (SSH)
- [x] Docker services binden aan 127.0.0.1 (niet extern bereikbaar)
- [x] fail2ban actief op sshd jail (ban 1h na 5 pogingen)

### Updates
- [x] unattended-upgrades actief (automatische security patches)

---

## Achtergrond & Geleerde Lessen

> Historische bevindingen. Nuttig als iets onverwachts gedraagt — de valkuilen zijn hier gedocumenteerd.

### installimage valkuilen

**`encrypt` vs `crypt`:** Het juiste keyword voor LUKS encryptie op de PART-regel is `crypt`. Met `encrypt` faalt `validate_vars()` stil. `dialog` probeert een foutvenster te tonen maar zonder TTY geeft het exit 1, waarna autosetup "Cancelled." print. Altijd `/root/debug.txt` lezen als dat gebeurt.

**`all` als laatste LV:** Een LV met grootte `all` (alle resterende ruimte) MOET de laatste zijn in de VG. installimage geeft anders: `LV size 'all' has to be on the last LV in VG vg0`.

**`nohup ... &`:** Start installimage ALTIJD in de foreground: `TERM=xterm installimage -a -c config.conf`. Met `nohup &` returnt het SSH-commando exit 0 (de start slaagde), maar het eigenlijke installimage process kan exit 1 geven. Dat zie je dan pas later.

### Dropbear valkuilen

**`cryptroot-unlock` leest niet van stdin:** `echo passphrase | ssh ... cryptroot-unlock` werkt niet. cryptroot-unlock leest van `/dev/console`. De correcte methode is schrijven naar de named FIFO: `echo -ne 'PASSPHRASE' > /lib/cryptsetup/passfifo`.

**ed25519 werkt wel in Ubuntu 24.04:** Oudere docs zeggen dat Dropbear alleen RSA ondersteunt. Ubuntu 24.04's `dropbear-initramfs` ondersteunt ook ed25519.

**SSH host key conflict:** Na nieuwe installatie heeft de server een nieuw SSH host key. Verwijder de oude entry: `ssh-keygen -R 5.9.10.215`.

### Docker + GPU valkuilen

**Infinity API ≠ TEI API:** Infinity (v2) ondersteunt alleen de OpenAI-compatible API (`POST /v1/embeddings` met `{"input": ..., "model": ...}`). De TEI API (`POST /embed` met `{"inputs": ...}`) werkt niet op Infinity. Let ook op het response formaat:
- TEI: `[[0.1, 0.2, ...]]` (array of arrays)
- Infinity: `{"data": [{"embedding": [0.1, ...], "index": 0}]}` — sorteer op `index`, resultaten kunnen out-of-order zijn.

**GHCR auth op gpu-01:** `ghcr.io/getklai/*` images zijn private. Credentials ophalen via `core-01:~/.docker/config.json` en dan `docker login ghcr.io`.

**VRAM verdeling:** Infinity met bge-m3 + bge-reranker-v2-m3 + Whisper large-v3-turbo = ~5.4 GB van 20 GB VRAM. BGE-M3 sparse draait op CPU (spaart ~1 GB VRAM, doet het goed op CPU).

### Monitoring valkuilen

**push-health.sh crashte bij ontbrekende KUMA tokens:** Het script had `set -uo pipefail` + variabelen zonder `:-` default (bijv. `${KUMA_TOKEN_CHAT}`). Bij een ontbrekende token crashte het script direct — alle infrastructuur-heartbeats (VEXA, PORTAL_API, etc.) werden nooit gestuurd. Fix: alle tokens naar `${KUMA_TOKEN_X:-}`, `push_exec`/`push_healthcheck` skippen nu bij leeg token.

**Docker containers bereiken host via 172.18.0.1:** Docker containers op `klai-net` (bridge gateway 172.18.0.1) kunnen de host bereiken via dit IP. `localhost` werkt NIET vanuit Docker containers voor host-poorten.

---

## Bronnen

- [Hetzner: installimage documentatie](https://docs.hetzner.com/robot/dedicated-server/operating-systems/installimage/)
- [Hetzner Community: Debian + LVM + Encrypted NVMe RAID](https://community.hetzner.com/tutorials/install-debian-with-lvm-encrypted-nvme-software-raid/)
- [GitHub: disk-encryption-hetzner (TheReal1604)](https://github.com/TheReal1604/disk-encryption-hetzner/blob/master/ubuntu/ubuntu_swraid_lvm_luks.md)
- [Ubuntu 24.04 Dropbear Setup (originell.org)](https://www.originell.org/til/ubuntu-24-dropbear-setup/)
- [nixCraft: How to unlock LUKS using Dropbear SSH](https://www.cyberciti.biz/security/how-to-unlock-luks-using-dropbear-ssh-keys-remotely-in-linux/)
- [GitHub: hetzneronline/installimage - functions.sh](https://github.com/hetzneronline/installimage/blob/master/functions.sh)
