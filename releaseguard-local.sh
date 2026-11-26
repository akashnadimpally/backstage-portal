#!/usr/bin/env bash
# =============================================================================
# releaseguard-local.sh — Run ReleaseGuard locally before pushing
#
# Usage:
#   ./releaseguard-local.sh [check|fix|sbom|harden|verify|install]
#
# Commands:
#   install   Download and install the ReleaseGuard CLI (once)
#   check     Scan dist/ artifacts for secrets and policy violations
#   fix       Apply safe hardening transforms (strip source maps, etc.)
#   sbom      Generate a CycloneDX SBOM from dist/ artifacts
#   harden    Run the full pipeline: fix → sbom → check
#   verify    Verify hardened artifacts pass policy
#
# Prerequisites:
#   - Build the project first:
#       yarn install --immutable && yarn tsc && yarn build:backend && yarn workspace app build
#   - Or run a subset: yarn build:backend / yarn workspace app build
#
# =============================================================================
set -euo pipefail

RELEASEGUARD_VERSION="${RELEASEGUARD_VERSION:-latest}"
RELEASEGUARD_BIN="${RELEASEGUARD_BIN:-$(command -v releaseguard 2>/dev/null || echo ./releaseguard)}"
CONFIG="${CONFIG:-.releaseguard.yml}"
SCAN_PATHS="packages/backend/dist packages/app/dist"

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[releaseguard]${NC} $*"; }
warn()    { echo -e "${YELLOW}[releaseguard]${NC} $*"; }
err()     { echo -e "${RED}[releaseguard] ERROR:${NC} $*" >&2; }

# ── Install ───────────────────────────────────────────────────────────────────
cmd_install() {
  info "Installing ReleaseGuard CLI..."
  local os arch tag
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "$arch" in
    x86_64)  arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) err "Unsupported architecture: $arch"; exit 1 ;;
  esac

  if [ "$RELEASEGUARD_VERSION" = "latest" ]; then
    tag=$(curl -fsSL https://api.github.com/repos/Helixar-AI/ReleaseGuard/releases/latest \
      | grep '"tag_name"' | sed -E 's/.*"tag_name":\s*"([^"]+)".*/\1/')
  else
    tag="$RELEASEGUARD_VERSION"
  fi
  info "Resolved version: $tag  os=$os  arch=$arch"

  local url="https://github.com/Helixar-AI/ReleaseGuard/releases/download/${tag}/releaseguard_${os}_${arch}.tar.gz"
  local tmpdir
  tmpdir=$(mktemp -d)
  curl -fsSL "$url" -o "$tmpdir/releaseguard.tar.gz"
  tar -xzf "$tmpdir/releaseguard.tar.gz" -C "$tmpdir"
  chmod +x "$tmpdir/releaseguard"

  # Prefer /usr/local/bin if writable, else install locally
  if [ -w /usr/local/bin ]; then
    mv "$tmpdir/releaseguard" /usr/local/bin/releaseguard
    info "Installed → /usr/local/bin/releaseguard"
  else
    mv "$tmpdir/releaseguard" ./releaseguard
    warn "Could not write to /usr/local/bin — installed locally as ./releaseguard"
    warn "Add '.' to your PATH or move it to a directory on PATH."
  fi
  rm -rf "$tmpdir"
  releaseguard --version || true
}

# ── Helpers ───────────────────────────────────────────────────────────────────
assert_binary() {
  if ! command -v releaseguard &>/dev/null && [ ! -x ./releaseguard ]; then
    err "ReleaseGuard CLI not found. Run: $0 install"
    exit 1
  fi
  RELEASEGUARD_BIN="$(command -v releaseguard 2>/dev/null || echo ./releaseguard)"
}

assert_dist() {
  local missing=()
  for p in packages/backend/dist packages/app/dist; do
    [ -d "$p" ] || missing+=("$p")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    err "Build artifacts not found: ${missing[*]}"
    err "Run: yarn install --immutable && yarn tsc && yarn build:backend && yarn workspace app build"
    exit 1
  fi
}

# ── Commands ──────────────────────────────────────────────────────────────────
cmd_check() {
  assert_binary
  assert_dist
  info "Scanning artifacts for secrets and policy violations..."
  "$RELEASEGUARD_BIN" check $SCAN_PATHS --config "$CONFIG"
  info "✅ Scan complete — no violations found."
}

cmd_fix() {
  assert_binary
  assert_dist
  info "Applying hardening transforms..."
  "$RELEASEGUARD_BIN" fix $SCAN_PATHS --config "$CONFIG"
  info "✅ Hardening transforms applied."
}

cmd_sbom() {
  assert_binary
  assert_dist
  info "Generating CycloneDX SBOM → sbom.json"
  "$RELEASEGUARD_BIN" sbom $SCAN_PATHS --config "$CONFIG" --output sbom.json
  info "✅ SBOM generated: $(wc -c < sbom.json) bytes"
}

cmd_harden() {
  assert_binary
  assert_dist
  info "=== ReleaseGuard full hardening pipeline ==="
  info "Step 1/3: Hardening transforms"
  "$RELEASEGUARD_BIN" fix $SCAN_PATHS --config "$CONFIG"
  info "Step 2/3: SBOM generation"
  "$RELEASEGUARD_BIN" sbom $SCAN_PATHS --config "$CONFIG" --output sbom.json
  info "Step 3/3: Policy check"
  "$RELEASEGUARD_BIN" check $SCAN_PATHS --config "$CONFIG"
  info "✅ Full hardening pipeline complete."
}

cmd_verify() {
  assert_binary
  assert_dist
  info "Verifying hardened artifacts..."
  "$RELEASEGUARD_BIN" verify $SCAN_PATHS --config "$CONFIG"
  info "✅ Artifacts verified clean."
}

usage() {
  echo "Usage: $0 [install|check|fix|sbom|harden|verify]"
  echo ""
  echo "  install   Download and install the ReleaseGuard CLI"
  echo "  check     Scan dist/ artifacts for secrets and policy violations"
  echo "  fix       Apply safe hardening transforms"
  echo "  sbom      Generate a CycloneDX SBOM"
  echo "  harden    Full pipeline: fix → sbom → check (recommended before release)"
  echo "  verify    Verify hardened artifacts pass policy"
  echo ""
  echo "Environment variables:"
  echo "  RELEASEGUARD_VERSION  CLI version to install (default: latest)"
  echo "  CONFIG                Path to policy config  (default: .releaseguard.yml)"
}

# ── Entry point ───────────────────────────────────────────────────────────────
CMD="${1:-harden}"
case "$CMD" in
  install) cmd_install ;;
  check)   cmd_check   ;;
  fix)     cmd_fix     ;;
  sbom)    cmd_sbom    ;;
  harden)  cmd_harden  ;;
  verify)  cmd_verify  ;;
  help|-h|--help) usage ;;
  *)
    err "Unknown command: $CMD"
    usage
    exit 1
    ;;
esac
