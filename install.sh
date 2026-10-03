#!/usr/bin/env bash
# Polarix installer — Linux host, everything the MCP server needs, in one go.
#
#   ./install.sh                 browser automation only (Python venv + Chromium)
#   ./install.sh --desktop       + KVM/libvirt for Windows/Linux guest VMs
#   ./install.sh --android       + adb (Android devices / emulators)
#   ./install.sh --appium        + Node + Appium server + UiAutomator2 driver
#   ./install.sh --all           everything above
#   ./install.sh --service       + systemd --user unit that starts the server at login
#
#   --yes            no questions
#   --no-sudo        never call sudo (system packages are reported, not installed)
#   --skip-browser   do not download Chromium (already there, or offline)
#   --llm openai|anthropic|all   also install that LLM SDK (browser_run_task, auto_sequence)
#   --python PATH    interpreter to use (default: newest python3.11+ found)
#
# Idempotent: run it again after a git pull. Ends with `polarix.doctor`, which lists
# what works, what is missing and the exact command that fixes each item.
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/polarix"
VENV="$BASE_DIR/.venv"

WITH_DESKTOP=0 WITH_ANDROID=0 WITH_APPIUM=0 WITH_SERVICE=0
ASSUME_YES=0 NO_SUDO=0 SKIP_BROWSER=0 LLM="" PYTHON_BIN=""

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --desktop) WITH_DESKTOP=1 ;;
    --android) WITH_ANDROID=1 ;;
    --appium) WITH_APPIUM=1 ;;
    --all) WITH_DESKTOP=1; WITH_ANDROID=1; WITH_APPIUM=1 ;;
    --service) WITH_SERVICE=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --no-sudo) NO_SUDO=1 ;;
    --skip-browser) SKIP_BROWSER=1 ;;
    --llm) LLM="${2:-}"; shift ;;
    --python) PYTHON_BIN="${2:-}"; shift ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# ----------------------------------------------------------------- helpers
say()  { printf '\033[1;34m[polarix]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m   ok  \033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  warn \033[0m %s\n' "$*"; }
todo() { printf '\033[1;31m  todo \033[0m %s\n' "$*"; }
TODOS=()
remember() { TODOS+=("$*"); todo "$*"; }

confirm() {
  [[ $ASSUME_YES -eq 1 ]] && return 0
  read -r -p "$1 [Y/n] " answer
  [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

PKG=""
if command -v dnf >/dev/null 2>&1; then PKG=dnf
elif command -v apt-get >/dev/null 2>&1; then PKG=apt
elif command -v pacman >/dev/null 2>&1; then PKG=pacman
elif command -v zypper >/dev/null 2>&1; then PKG=zypper
fi

can_sudo() {
  [[ $NO_SUDO -eq 1 ]] && return 1
  command -v sudo >/dev/null 2>&1 || return 1
  if sudo -n true 2>/dev/null; then return 0; fi
  [[ $ASSUME_YES -eq 1 ]] && return 1   # no prompt possible
  return 0  # sudo will ask for the password interactively
}

# install_pkgs "<purpose>" dnf-pkgs -- apt-pkgs -- pacman-pkgs -- zypper-pkgs
install_pkgs() {
  local purpose="$1"; shift
  local -a dnf_p=() apt_p=() pac_p=() zyp_p=()
  local bucket=dnf
  for item in "$@"; do
    if [[ "$item" == "--" ]]; then
      case $bucket in dnf) bucket=apt ;; apt) bucket=pac ;; pac) bucket=zyp ;; esac
      continue
    fi
    case $bucket in
      dnf) dnf_p+=("$item") ;; apt) apt_p+=("$item") ;;
      pac) pac_p+=("$item") ;; zyp) zyp_p+=("$item") ;;
    esac
  done
  local -a pkgs=() cmd=()
  case $PKG in
    dnf) pkgs=("${dnf_p[@]}"); cmd=(sudo dnf install -y) ;;
    apt) pkgs=("${apt_p[@]}"); cmd=(sudo apt-get install -y) ;;
    pacman) pkgs=("${pac_p[@]}"); cmd=(sudo pacman -S --noconfirm --needed) ;;
    zypper) pkgs=("${zyp_p[@]}"); cmd=(sudo zypper install -y) ;;
    *) remember "$purpose: no known package manager; install manually: ${dnf_p[*]}"; return 0 ;;
  esac
  [[ ${#pkgs[@]} -eq 0 ]] && return 0
  if can_sudo && confirm "Install system packages for $purpose (${pkgs[*]})?"; then
    [[ $PKG == apt ]] && sudo apt-get update -qq
    "${cmd[@]}" "${pkgs[@]}"
    ok "$purpose: system packages installed"
  else
    remember "$purpose: run  ${cmd[*]} ${pkgs[*]}"
  fi
}

# ----------------------------------------------------------------- python
say "Python"
find_python() {
  [[ -n "$PYTHON_BIN" ]] && { echo "$PYTHON_BIN"; return; }
  for cand in python3.13 python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1 \
       && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$cand"; return
    fi
  done
}
PY="$(find_python || true)"
if [[ -z "$PY" ]]; then
  install_pkgs "Python 3.11" python3.11 python3.11-devel -- python3.11 python3.11-venv python3.11-dev -- python -- python311
  PY="$(find_python || true)"
  [[ -z "$PY" ]] && { echo "Python 3.11+ is required and was not found." >&2; exit 1; }
fi
ok "using $("$PY" --version) at $(command -v "$PY")"

if [[ ! -x "$VENV/bin/python" ]]; then
  "$PY" -m venv "$VENV"
  ok "virtualenv created at $VENV"
else
  ok "virtualenv already at $VENV"
fi
VPY="$VENV/bin/python"
"$VPY" -m pip install --quiet --upgrade pip
EXTRAS=""
case "$LLM" in
  openai) EXTRAS="[openai]" ;; anthropic) EXTRAS="[anthropic]" ;;
  all) EXTRAS="[openai,anthropic]" ;; "") ;;
  *) echo "--llm must be openai, anthropic or all" >&2; exit 2 ;;
esac
"$VPY" -m pip install --quiet -e "$BASE_DIR$EXTRAS"
ok "polarix-mcp installed into the virtualenv${EXTRAS:+ with $EXTRAS}"

# ----------------------------------------------------------------- chromium
say "Chromium (Playwright)"
if [[ $SKIP_BROWSER -eq 1 ]]; then
  warn "skipped (--skip-browser)"
else
  if [[ $PKG == apt ]] && can_sudo; then
    "$VPY" -m playwright install --with-deps chromium && ok "Chromium + system libraries"
  else
    "$VPY" -m playwright install chromium && ok "Chromium downloaded"
    if [[ $PKG == dnf ]]; then
      install_pkgs "Chromium runtime libraries" nss atk at-spi2-atk cups-libs libdrm libxkbcommon \
        libXcomposite libXdamage libXrandr mesa-libgbm pango alsa-lib -- -- -- --
    fi
  fi
fi

# ----------------------------------------------------------------- desktop (KVM/libvirt)
if [[ $WITH_DESKTOP -eq 1 ]]; then
  say "Desktop guests — KVM/libvirt"
  if [[ -e /dev/kvm ]]; then ok "/dev/kvm present"; else warn "/dev/kvm missing: enable VT-x/AMD-V in the firmware (or use a .metal cloud instance)"; fi
  install_pkgs "libvirt/QEMU" libvirt qemu-kvm virt-install virt-viewer edk2-ovmf swtpm libvirt-client \
    -- qemu-kvm libvirt-daemon-system libvirt-clients virtinst virt-viewer ovmf swtpm \
    -- libvirt qemu-full virt-install virt-viewer edk2-ovmf swtpm \
    -- libvirt qemu-kvm virt-install virt-viewer qemu-ovmf-x86_64 swtpm
  if command -v virsh >/dev/null 2>&1; then
    if virsh --connect qemu:///system list --all >/dev/null 2>&1; then
      ok "libvirt system daemon reachable"
    else
      if can_sudo && confirm "Enable the libvirt daemon and add $USER to the libvirt group?"; then
        if systemctl list-unit-files virtqemud.socket >/dev/null 2>&1 && systemctl list-unit-files | grep -q '^virtqemud.socket'; then
          sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket 2>/dev/null \
            || sudo systemctl enable --now libvirtd
        else
          sudo systemctl enable --now libvirtd
        fi
        sudo usermod -aG libvirt "$USER"
        remember "log out and in again so the libvirt group applies (then virsh -c qemu:///system list works)"
      else
        remember "libvirt daemon: sudo systemctl enable --now virtqemud.socket virtnetworkd.socket virtstoraged.socket && sudo usermod -aG libvirt $USER  (or POLARIX_LIBVIRT_URI=qemu:///session for user mode)"
      fi
    fi
  fi
fi

# ----------------------------------------------------------------- android (adb)
if [[ $WITH_ANDROID -eq 1 ]]; then
  say "Android — adb"
  SDK_ADB="$HOME/Android/Sdk/platform-tools/adb"
  if command -v adb >/dev/null 2>&1; then
    ok "adb at $(command -v adb)"
  elif [[ -x "$SDK_ADB" ]]; then
    ok "adb from the Android SDK at $SDK_ADB (set POLARIX_ADB=$SDK_ADB or add platform-tools to PATH)"
  else
    install_pkgs "adb" android-tools -- adb -- android-tools -- android-tools
  fi
fi

# ----------------------------------------------------------------- appium
if [[ $WITH_APPIUM -eq 1 ]]; then
  say "Appium — mobile through WebDriver"
  if ! command -v node >/dev/null 2>&1 || ! node -e 'process.exit(parseInt(process.versions.node) >= 18 ? 0 : 1)'; then
    install_pkgs "Node.js 18+" nodejs npm -- nodejs npm -- nodejs npm -- nodejs22 npm22
  fi
  if command -v npm >/dev/null 2>&1; then
    if command -v appium >/dev/null 2>&1; then
      ok "appium at $(command -v appium) ($(appium --version 2>/dev/null || echo '?'))"
    else
      NPM_PREFIX="$(npm config get prefix 2>/dev/null || echo /usr/local)"
      if [[ -w "$NPM_PREFIX/lib" || -w "$NPM_PREFIX" ]]; then
        npm install -g appium && ok "appium installed"
      elif can_sudo && confirm "Install appium globally with sudo npm?"; then
        sudo npm install -g appium && ok "appium installed"
      else
        remember "appium: npm config set prefix ~/.npm-global && export PATH=~/.npm-global/bin:\$PATH && npm install -g appium"
      fi
    fi
    if command -v appium >/dev/null 2>&1; then
      if appium driver list --installed 2>&1 | grep -q uiautomator2; then
        ok "appium driver uiautomator2 present"
      else
        appium driver install uiautomator2 && ok "appium driver uiautomator2 installed" \
          || remember "appium driver install uiautomator2"
      fi
    fi
  else
    remember "npm not found; install Node.js 18+ then: npm install -g appium && appium driver install uiautomator2"
  fi
  warn "iPhone: the Appium server with the XCUITest driver must run on a Mac with Xcode — point a target's server_url at it"
fi

# ----------------------------------------------------------------- config
say "Configuration"
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$BASE_DIR/.env" ]]; then
  cat > "$BASE_DIR/.env" <<'ENV'
# Polarix MCP — edit and restart ./start.sh. This file is git-ignored.
MCP_HOST=127.0.0.1
MCP_PORT=8016
MCP_TRANSPORT=streamable-http

# LLM for browser_run_task / *_auto_sequence / vision (at least one key)
#OPENAI_API_KEY=sk-...
#ANTHROPIC_API_KEY=sk-ant-...
BROWSER_USE_MODEL=gpt-4o-mini
BROWSER_HEADLESS=true

# Desktop: auto (pywinauto when Polarix runs on Windows) | remote | android | appium | fake
POLARIX_DESKTOP_DRIVER=fake
#POLARIX_DESKTOP_AGENT_URL=http://192.168.122.15:8020
#POLARIX_AGENT_TOKEN=change-me
#POLARIX_TARGET=win11

# VM backends: auto | libvirt | virtualbox | android
POLARIX_VM_BACKEND=auto
#POLARIX_LIBVIRT_URI=qemu:///session     # user mode, no root
#POLARIX_ADB=/home/you/Android/Sdk/platform-tools/adb
#POLARIX_APPIUM_URL=http://127.0.0.1:4723

# Named targets and test environments
#POLARIX_TARGETS_FILE=~/.config/polarix/targets.json
#POLARIX_ENVIRONMENTS_FILE=~/.config/polarix/environments.json
ENV
  ok ".env written (edit the keys and drivers you use)"
else
  ok ".env kept"
fi
if [[ ! -f "$CONFIG_DIR/targets.example.json" ]]; then
  cat > "$CONFIG_DIR/targets.example.json" <<'JSON'
{
  "win11":  {"driver": "remote", "agent_url": "http://192.168.122.15:8020", "token": "SECRET",
             "os": "windows", "vm": {"backend": "libvirt", "name": "win11-lab", "snapshot": "clean"}},
  "ubuntu": {"driver": "remote", "agent_url": "http://192.168.122.20:8020", "os": "linux"},
  "pixel":  {"driver": "android", "serial": "emulator-5554"},
  "iphone": {"driver": "appium", "server_url": "http://mac-mini.lan:4723",
             "capabilities": {"platformName": "iOS", "appium:automationName": "XCUITest",
                              "appium:deviceName": "iPhone 15", "appium:udid": "..."}}
}
JSON
fi
if [[ ! -f "$CONFIG_DIR/environments.example.json" ]]; then
  cat > "$CONFIG_DIR/environments.example.json" <<'JSON'
{
  "win11-editor": {
    "target": "win11",
    "install":   [{"action": "shell", "command": "winget install --id Example.Editor -e --accept-package-agreements", "timeout": 600}],
    "uninstall": [{"action": "shell", "command": "winget uninstall --id Example.Editor"}],
    "reset":     [{"action": "shell", "command": "del /q %APPDATA%\\Editor\\*"}],
    "variables": {"app": "C:/Program Files/Editor/editor.exe"}
  }
}
JSON
fi
ok "examples in $CONFIG_DIR (copy to targets.json / environments.json and edit)"

# ----------------------------------------------------------------- service
if [[ $WITH_SERVICE -eq 1 ]]; then
  say "systemd --user service"
  UNIT_DIR="$HOME/.config/systemd/user"; mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/polarix-mcp.service" <<UNIT
[Unit]
Description=Polarix MCP server (browser + desktop automation)
After=network.target

[Service]
Type=simple
WorkingDirectory=$BASE_DIR
ExecStart=$BASE_DIR/start.sh
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now polarix-mcp.service && ok "polarix-mcp.service enabled and started"
  loginctl show-user "$USER" 2>/dev/null | grep -q 'Linger=yes' \
    || remember "to keep the service running without a login session: sudo loginctl enable-linger $USER"
fi

# ----------------------------------------------------------------- doctor
say "Doctor"
"$VPY" -m polarix.doctor || true

echo
say "Connect your client to  http://${MCP_HOST:-127.0.0.1}:${MCP_PORT:-8016}/mcp"
cat <<'SNIP'
   Claude Code / Cline / Zed:  {"polarix": {"type": "streamable-http", "url": "http://127.0.0.1:8016/mcp"}}
   Goose:                      {"polarix": {"type": "streamable_http", "uri": "http://127.0.0.1:8016/mcp"}}
SNIP
if [[ $WITH_SERVICE -eq 0 ]]; then say "Start the server with  ./start.sh"; fi
if [[ ${#TODOS[@]} -gt 0 ]]; then
  echo; say "Still to do by hand:"
  for t in "${TODOS[@]}"; do todo "$t"; done
fi
