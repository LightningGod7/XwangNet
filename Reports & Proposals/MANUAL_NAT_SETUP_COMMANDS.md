# Manual NAT+Bridge Setup - Complete Command List

## Overview
This guide sets up a Docker bridge network with edge device (AXE75) accessible via external LAN IP using NAT, with Suricata IDS monitoring all traffic.

**Architecture:**
```
External (192.168.7.7) → ens18 → macvlan0 (192.168.7.242)
                                      ↓ DNAT
                        Docker Bridge Network (172.19.0.0/16)
                          ├── Suricata (172.19.0.2)
                          └── AXE75 Edge (172.19.0.3)
```

---

## Prerequisites

```bash
# Verify parent interface exists
ip link show ens18

# Verify you have necessary permissions
sudo iptables -L -n | head -5

# Check dhclient is installed
which dhclient
```

---

## STEP 1: Create Docker Bridge Network

```bash
# Create bridge network with custom subnet
docker network create \
  --driver bridge \
  --subnet 172.19.0.0/16 \
  --gateway 172.19.0.1 \
  test-deployment-net

# Verify network created
docker network ls | grep test-deployment-net

# Inspect network details
docker network inspect test-deployment-net --format '{{.Id}}'
```

**Expected output:** Network ID (e.g., `431706091c0d...`)

---

## STEP 2: Create Macvlan Interface

```bash
# Create macvlan interface in bridge mode
sudo ip link add macvlan0 link ens18 type macvlan mode bridge

# Bring interface up
sudo ip link set macvlan0 up

# Verify interface created
ip link show macvlan0

# Check interface details (should show: macvlan mode bridge)
ip -d link show macvlan0 | grep macvlan
```

**Expected output:**
```
macvlan mode bridge bcqueuelen 1000
```

---

## STEP 3: Request DHCP IP for Macvlan Interface

```bash
# Request DHCP IP (takes 10-30 seconds)
sudo dhclient -v macvlan0

# Wait for DHCP to complete
sleep 3

# Check assigned IP
ip addr show macvlan0 | grep "inet "

# Store IP in variable for later use
MACVLAN_IP=$(ip addr show macvlan0 | grep "inet " | awk '{print $2}' | cut -d/ -f1)
echo "Macvlan IP: $MACVLAN_IP"

# Test connectivity from macvlan interface
ping -c 2 -I macvlan0 192.168.7.1
```

**Expected output:** DHCP assigns IP (e.g., `192.168.7.242`)

---

## STEP 4: Start Suricata IDS Container

```bash
# Start Suricata with static IP as gateway
docker run -d \
  --name suricata-gateway \
  --network test-deployment-net \
  --ip 172.19.0.2 \
  --cap-add NET_ADMIN \
  --cap-add NET_RAW \
  jasonish/suricata:latest \
  -i eth0

# Wait for container to start
sleep 3

# Verify Suricata is running
docker ps | grep suricata-gateway

# Check Suricata logs
docker logs suricata-gateway | head -20
```

**Expected output:** Container running, Suricata initializing on eth0

---

## STEP 5: Start AXE75 Edge Device Container

```bash
# Start AXE75 with static IP (edge device)
docker run -d \
  --name axe75-edge \
  --network test-deployment-net \
  --ip 172.19.0.3 \
  --privileged \
  axe75-v2-ctf:latest

# Wait for container to start
sleep 5

# Verify AXE75 is running
docker ps | grep axe75-edge

# Check if web interface is responding
curl -I http://172.19.0.3
```

**Expected output:** Container running, HTTP response on port 80

---

## STEP 6: Verify Network Topology

```bash
# List all containers in the network
docker network inspect test-deployment-net \
  --format '{{range .Containers}}{{.Name}}: {{.IPv4Address}}{{"\n"}}{{end}}'

# Test inter-container connectivity
docker exec suricata-gateway ping -c 2 172.19.0.3

# Verify direct access from host
curl http://172.19.0.3 | head -5
```

**Expected output:**
```
suricata-gateway: 172.19.0.2/16
axe75-edge: 172.19.0.3/16
```

---

## STEP 7: Configure NAT Rules (THE CRITICAL SETUP)

```bash
# Get network variables
MACVLAN_IP=$(ip addr show macvlan0 | grep "inet " | awk '{print $2}' | cut -d/ -f1)
CONTAINER_IP="172.19.0.3"
BRIDGE_IF="br-$(docker network inspect test-deployment-net --format '{{.Id}}' | cut -c1-12)"

echo "=== Configuration ==="
echo "Macvlan IP:    $MACVLAN_IP"
echo "Container IP:  $CONTAINER_IP"
echo "Bridge:        $BRIDGE_IF"
echo ""

# =====================================
# Rule 1: DNAT - Incoming traffic
# =====================================
# Forward all traffic to macvlan IP → container IP
sudo iptables -t nat -A PREROUTING -d $MACVLAN_IP -j DNAT --to-destination $CONTAINER_IP

echo "✓ DNAT rule added"

# =====================================
# Rule 2: SNAT - Outgoing traffic
# =====================================
# Make replies appear to come from macvlan IP
sudo iptables -t nat -A POSTROUTING -s $CONTAINER_IP -j SNAT --to-source $MACVLAN_IP

echo "✓ SNAT rule added"

# =====================================
# Rule 3: DOCKER-USER - Allow incoming
# =====================================
# Accept traffic TO the container
sudo iptables -I DOCKER-USER -d $CONTAINER_IP -j ACCEPT

echo "✓ DOCKER-USER incoming rule added"

# =====================================
# Rule 4: DOCKER-USER - Allow outgoing via macvlan0
# =====================================
# Force outbound traffic through macvlan0 only
sudo iptables -I DOCKER-USER -o macvlan0 -s $CONTAINER_IP -j ACCEPT

echo "✓ DOCKER-USER outgoing (macvlan0) rule added"

# =====================================
# Rule 5: FORWARD - Allow outbound from bridge
# =====================================
# THE CRITICAL FIX: Allow reply packets to exit Docker bridge
sudo iptables -I FORWARD -i $BRIDGE_IF -s $CONTAINER_IP -j ACCEPT

echo "✓ FORWARD rule added (THE CRITICAL FIX)"

echo ""
echo "=== All NAT rules configured successfully ==="
```

---

## STEP 8: Verify NAT Rules Are Active

```bash
# Check PREROUTING (DNAT)
echo "=== PREROUTING (DNAT) ==="
sudo iptables -t nat -L PREROUTING -n -v | grep $MACVLAN_IP

# Check POSTROUTING (SNAT)
echo "=== POSTROUTING (SNAT) ==="
sudo iptables -t nat -L POSTROUTING -n -v | grep $CONTAINER_IP

# Check DOCKER-USER rules
echo "=== DOCKER-USER ==="
sudo iptables -L DOCKER-USER -n -v --line-numbers | grep $CONTAINER_IP

# Check FORWARD rules
echo "=== FORWARD ==="
sudo iptables -L FORWARD -n -v --line-numbers | grep $CONTAINER_IP

echo ""
echo "All rules verified ✓"
```

---

## STEP 9: Test Connectivity

### From XwangNet Host:

```bash
# Test direct container access
curl http://172.19.0.3

# Display external access info
echo "==================================="
echo "Edge device is accessible at:"
echo "  LAN IP:      $MACVLAN_IP"
echo "  Internal IP: 172.19.0.3"
echo "==================================="
```

### From External Host (192.168.7.7):

```bash
# Replace with your actual macvlan IP
EXTERNAL_IP="192.168.7.242"

# Test ICMP connectivity
ping -c 5 $EXTERNAL_IP

# Test HTTP connectivity
curl http://$EXTERNAL_IP

# Test with verbose output
curl -v http://$EXTERNAL_IP
```

### Monitor Traffic (Optional):

```bash
# Terminal 1: Monitor macvlan0 interface
sudo tcpdump -i macvlan0 -n 'host 192.168.7.7' -v

# Terminal 2: Monitor Docker bridge
sudo tcpdump -i $BRIDGE_IF -n 'host 172.19.0.3' -v

# Terminal 3: Monitor all interfaces
sudo tcpdump -i any -n 'host 192.168.7.7 or host 172.19.0.3' -v
```

**Expected results:**
- ✅ Ping: 0% packet loss
- ✅ Curl: Returns HTML content
- ✅ tcpdump: Shows bidirectional traffic (requests AND replies)

---

## STEP 10: Verify Suricata is Monitoring Traffic

```bash
# Check Suricata logs for captured traffic
docker exec suricata-gateway cat /var/log/suricata/eve.json | tail -20

# Monitor Suricata in real-time
docker logs -f suricata-gateway
```

**Expected:** Suricata should log traffic to/from 172.19.0.3

---

## Complete iptables Rules Summary

For reference, here are all 5 iptables rules in order:

```bash
# Variables
MACVLAN_IP="192.168.7.242"      # Your DHCP-assigned IP
CONTAINER_IP="172.19.0.3"       # Edge device internal IP
BRIDGE_IF="br-431706091c0d"     # Docker bridge interface

# Rule 1: DNAT (Incoming)
sudo iptables -t nat -A PREROUTING -d $MACVLAN_IP -j DNAT --to-destination $CONTAINER_IP

# Rule 2: SNAT (Outgoing)
sudo iptables -t nat -A POSTROUTING -s $CONTAINER_IP -j SNAT --to-source $MACVLAN_IP

# Rule 3: DOCKER-USER Accept (Incoming)
sudo iptables -I DOCKER-USER -d $CONTAINER_IP -j ACCEPT

# Rule 4: DOCKER-USER Accept (Outgoing via macvlan0)
sudo iptables -I DOCKER-USER -o macvlan0 -s $CONTAINER_IP -j ACCEPT

# Rule 5: FORWARD Accept (Outgoing from bridge) - THE CRITICAL FIX
sudo iptables -I FORWARD -i $BRIDGE_IF -s $CONTAINER_IP -j ACCEPT
```

**Why each rule is needed:**
- **Rule 1 (DNAT):** Rewrites destination IP so external traffic reaches container
- **Rule 2 (SNAT):** Rewrites source IP so replies appear to come from macvlan IP
- **Rule 3 (DOCKER-USER):** Allows incoming traffic to pass Docker's filters
- **Rule 4 (DOCKER-USER):** Forces outbound traffic to use macvlan0 (not ens18)
- **Rule 5 (FORWARD):** **THE KEY FIX** - Allows reply packets to exit Docker bridge

---

## CLEANUP (When Done Testing)

```bash
# Get variables
MACVLAN_IP=$(ip addr show macvlan0 2>/dev/null | grep "inet " | awk '{print $2}' | cut -d/ -f1)
CONTAINER_IP="172.19.0.3"
BRIDGE_IF="br-$(docker network inspect test-deployment-net 2>/dev/null --format '{{.Id}}' | cut -c1-12)"

echo "Cleaning up NAT setup..."

# Remove iptables rules (in reverse order)
sudo iptables -D FORWARD -i $BRIDGE_IF -s $CONTAINER_IP -j ACCEPT 2>/dev/null
sudo iptables -D DOCKER-USER -o macvlan0 -s $CONTAINER_IP -j ACCEPT 2>/dev/null
sudo iptables -D DOCKER-USER -d $CONTAINER_IP -j ACCEPT 2>/dev/null
sudo iptables -t nat -D POSTROUTING -s $CONTAINER_IP -j SNAT --to-source $MACVLAN_IP 2>/dev/null
sudo iptables -t nat -D PREROUTING -d $MACVLAN_IP -j DNAT --to-destination $CONTAINER_IP 2>/dev/null

echo "✓ iptables rules removed"

# Stop and remove containers
docker stop axe75-edge suricata-gateway 2>/dev/null
docker rm axe75-edge suricata-gateway 2>/dev/null

echo "✓ Containers removed"

# Remove Docker network
docker network rm test-deployment-net 2>/dev/null

echo "✓ Docker network removed"

# Release DHCP lease and remove interface
sudo dhclient -r macvlan0 2>/dev/null
sudo ip link delete macvlan0 2>/dev/null

echo "✓ Macvlan interface removed"
echo ""
echo "Cleanup complete!"
```

---

## Troubleshooting

### Issue: DHCP doesn't assign IP

```bash
# Check DHCP server logs on router
# Or manually assign IP:
sudo ip addr add 192.168.7.242/24 dev macvlan0
```

### Issue: Cannot ping from external host

```bash
# Verify iptables rules are active
sudo iptables -t nat -L -n -v | grep 192.168.7.242

# Check FORWARD rule counter is increasing
sudo iptables -L FORWARD -n -v --line-numbers | grep 172.19.0.3

# Send gratuitous ARP
ping -c 3 -I macvlan0 192.168.7.1
```

### Issue: Can ping but cannot access web server

```bash
# Check container is listening on port 80
docker exec axe75-edge netstat -tuln | grep :80

# Check if firewall is blocking
curl -v http://172.19.0.3
```

### Issue: Traffic not going through macvlan0

```bash
# Check routing
ip route get 192.168.7.7 from 172.19.0.3 iif $BRIDGE_IF

# Should show: "dev macvlan0"
# If shows ens18, remove conflicting DOCKER-USER rule
```

---

## Quick Reference - One-Line Setup

For experienced users, here's the complete setup in one block:

```bash
# Quick Setup (copy all at once)
docker network create --driver bridge --subnet 172.19.0.0/16 --gateway 172.19.0.1 test-deployment-net && \
sudo ip link add macvlan0 link ens18 type macvlan mode bridge && \
sudo ip link set macvlan0 up && \
sudo dhclient -v macvlan0 && \
sleep 3 && \
docker run -d --name suricata-gateway --network test-deployment-net --ip 172.19.0.2 --cap-add NET_ADMIN --cap-add NET_RAW jasonish/suricata:latest -i eth0 && \
docker run -d --name axe75-edge --network test-deployment-net --ip 172.19.0.3 --privileged axe75-v2-ctf:latest && \
sleep 3 && \
MACVLAN_IP=$(ip addr show macvlan0 | grep "inet " | awk '{print $2}' | cut -d/ -f1) && \
BRIDGE_IF="br-$(docker network inspect test-deployment-net --format '{{.Id}}' | cut -c1-12)" && \
sudo iptables -t nat -A PREROUTING -d $MACVLAN_IP -j DNAT --to-destination 172.19.0.3 && \
sudo iptables -t nat -A POSTROUTING -s 172.19.0.3 -j SNAT --to-source $MACVLAN_IP && \
sudo iptables -I DOCKER-USER -d 172.19.0.3 -j ACCEPT && \
sudo iptables -I DOCKER-USER -o macvlan0 -s 172.19.0.3 -j ACCEPT && \
sudo iptables -I FORWARD -i $BRIDGE_IF -s 172.19.0.3 -j ACCEPT && \
echo "Setup complete! Edge device accessible at: $MACVLAN_IP"
```

---

## Notes

- **Suricata Monitoring:** All traffic to/from 172.19.0.3 passes through the bridge, visible to Suricata at 172.19.0.2
- **Security:** Edge device (172.19.0.3) is directly exposed to internet via LAN IP
- **Scalability:** Repeat with macvlan1, macvlan2... for multiple deployments
- **Persistence:** iptables rules are NOT persistent across reboots - save with `iptables-save`
- **Router:** May need to configure port forwarding on router for WAN access

---

## Implementation in XwangNet

See `/tmp/CRITICAL_FIX_FOR_NAT_MANAGER.md` for Python implementation details.

Key services to implement:
- `InterfaceManager` - Handles macvlan creation, DHCP, cleanup
- `NATManager` - Handles iptables rule management
- Integration in `views.py` for deployment lifecycle

---

**Last Updated:** October 27, 2025  
**Tested On:** Ubuntu 22.04.5 LTS, Docker 27.x, XwangNet Server

