#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[FIX]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
die()  { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

CONFIG=/etc/containerd/config.toml

[ "$(id -u)" -eq 0 ] || die "Run as root: sudo bash fix-containerd.sh"
[ -f "$CONFIG" ] || die "Config not found: $CONFIG"

log "Backing up config to ${CONFIG}.bak"
cp "$CONFIG" "${CONFIG}.bak"

log "Checking for conflicting mirrors + config_path..."
HAS_CONFIG_PATH=$(grep -c 'config_path' "$CONFIG" || true)
HAS_MIRRORS=$(grep -c '\[.*registry\.mirrors' "$CONFIG" || true)

if [ "$HAS_CONFIG_PATH" -eq 0 ] && [ "$HAS_MIRRORS" -eq 0 ]; then
  warn "Neither config_path nor mirrors found — nothing to fix in config."
else
  log "Found conflicting registry config. Removing [registry.mirrors] blocks..."

  # Remove the mirrors block using Python (handles multi-line TOML blocks safely)
  python3 - <<'PYEOF'
import re, sys

with open('/etc/containerd/config.toml', 'r') as f:
    content = f.read()

# Remove the entire [plugins."io.containerd.grpc.v1.cri".registry.mirrors] section
# and all its sub-sections, up to the next top-level or same-level section
cleaned = re.sub(
    r'\[plugins\."io\.containerd\.grpc\.v1\.cri"\.registry\.mirrors\].*?(?=\n\[(?!plugins\."io\.containerd\.grpc\.v1\.cri"\.registry\.mirrors)|\Z)',
    '',
    content,
    flags=re.DOTALL
)

# Also remove individual mirror host sub-tables
cleaned = re.sub(
    r'\[plugins\."io\.containerd\.grpc\.v1\.cri"\.registry\.mirrors\.[^\]]+\].*?(?=\n\[|\Z)',
    '',
    cleaned,
    flags=re.DOTALL
)

# Clean up excessive blank lines left behind
cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

with open('/etc/containerd/config.toml', 'w') as f:
    f.write(cleaned)

print("Config cleaned successfully.")
PYEOF

  log "Verifying fix..."
  if grep -q 'registry\.mirrors' "$CONFIG"; then
    warn "Some mirrors entries may remain. Check ${CONFIG} manually."
    grep -n 'mirrors' "$CONFIG" || true
  else
    log "No mirrors entries remaining. Config looks clean."
  fi
fi

log "Restarting containerd..."
systemctl restart containerd
sleep 6

log "Checking containerd CRI plugin..."
if crictl --runtime-endpoint unix:///run/containerd/containerd.sock info &>/dev/null; then
  log "containerd CRI is responding correctly."
else
  die "containerd CRI still not responding. Check: sudo journalctl -u containerd -n 30"
fi

log "Restarting kubelet..."
systemctl restart kubelet

log "Waiting for kubelet to come up (up to 60s)..."
for i in $(seq 1 12); do
  sleep 5
  STATUS=$(systemctl is-active kubelet 2>/dev/null || true)
  if [ "$STATUS" = "active" ]; then
    log "kubelet is active."
    break
  fi
  if [ "$i" -eq 12 ]; then
    die "kubelet failed to start. Check: sudo journalctl -u kubelet -n 30"
  fi
  echo "  waiting... ($((i*5))s)"
done

log "Waiting for API server to respond (up to 90s)..."
for i in $(seq 1 18); do
  sleep 5
  if kubectl get nodes --request-timeout=5s &>/dev/null; then
    log "API server is responding!"
    break
  fi
  if [ "$i" -eq 18 ]; then
    warn "API server not responding after 90s — it may need more time. Try: kubectl get nodes"
  fi
  echo "  waiting for API server... ($((i*5))s)"
done

echo ""
log "=== CLUSTER STATUS ==="
kubectl get nodes 2>/dev/null || warn "kubectl not ready yet — try again in 30s"
echo ""
log "=== RUNNING CONTAINERS ==="
crictl --runtime-endpoint unix:///run/containerd/containerd.sock ps 2>/dev/null | head -10 || true
echo ""
log "Done. If nodes show NotReady, wait 2-3 minutes and check again."
log "Original config saved at: ${CONFIG}.bak"
