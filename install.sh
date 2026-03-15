#!/bin/bash
# install.sh - Setup script for linux-stt-hotkey
# Run: chmod +x install.sh && ./install.sh

set -e

echo "=== Linux Speech-to-Text Hotkey - Installer ==="
echo ""

# System dependencies
echo "[1/4] Installing system dependencies..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        python3-pip \
        libportaudio2 portaudio19-dev \
        xdotool xclip \
        libnotify-bin \
        wtype wl-clipboard
elif command -v dnf &>/dev/null; then
    sudo dnf install -y \
        python3-pip \
        portaudio portaudio-devel \
        xdotool xclip \
        libnotify \
        wtype wl-clipboard
elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm \
        python-pip \
        portaudio \
        xdotool xclip \
        libnotify \
        wtype wl-clipboard
else
    echo "WARNING: Unknown package manager. Install manually:"
    echo "  - python pip"
    echo "  - portaudio (libportaudio2)"
    echo "  - xdotool, xclip"
    echo "  - wtype, wl-clipboard (Wayland text paste)"
    echo "  - libnotify (notify-send)"
fi

# Python dependencies
echo ""
echo "[2/4] Installing Python dependencies..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
LAUNCHER_PATH="$BIN_DIR/stt-hotkey"
DESKTOP_PATH="$APP_DIR/linux-stt-hotkey.desktop"
AUTOSTART_PATH="$AUTOSTART_DIR/linux-stt-hotkey.desktop"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"

# Pre-download the model
echo ""
echo "[3/4] Pre-downloading Whisper base.en model (~80MB, one-time)..."
"$VENV_DIR/bin/python" -c "
from faster_whisper import WhisperModel
print('  Downloading and converting model...')
WhisperModel('base.en', device='cpu', compute_type='int8')
print('  Model cached successfully.')
"

# Make main script executable
chmod +x "$SCRIPT_DIR/stt_hotkey.py"

echo ""
echo "[4/4] Installing launcher and desktop entry..."
mkdir -p "$BIN_DIR" "$APP_DIR" "$AUTOSTART_DIR"

cat > "$LAUNCHER_PATH" <<EOF
#!/bin/sh
exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/stt_hotkey.py" "\$@"
EOF
chmod +x "$LAUNCHER_PATH"

cat > "$DESKTOP_PATH" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Linux STT Hotkey
Comment=Offline push-to-talk speech to text
Exec=$LAUNCHER_PATH run
Terminal=false
Categories=Utility;
StartupNotify=false
EOF

cat > "$AUTOSTART_PATH" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Linux STT Hotkey
Comment=Start offline push-to-talk speech to text on login
Exec=$LAUNCHER_PATH run
Terminal=false
X-GNOME-Autostart-enabled=true
StartupNotify=false
EOF

echo ""
echo "=== Installation Complete ==="
echo ""
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    echo "Wayland session detected."
    echo "  For global hotkeys, add your user to the input group:"
    echo "    sudo usermod -aG input \$USER"
    echo "  Try this first to start a shell with the new group, without logging out:"
    echo "    newgrp input"
    echo "  Then run:"
    echo "    stt-hotkey start --backend evdev"
    echo "  If that still fails, log out and back in once."
    echo ""
fi

echo "Usage:"
echo "  stt-hotkey           # start in background"
echo "  stt-hotkey run       # run in foreground"
echo "  stt-hotkey stop"
echo "  stt-hotkey status"
echo ""
echo "Installed launcher:"
echo "  $LAUNCHER_PATH"
echo ""
echo "Autostart enabled:"
echo "  $AUTOSTART_PATH"
echo ""
echo "Default hotkey: F9 (push-to-talk)"
echo "Change with:    stt-hotkey start --hotkey f8"
echo ""
echo "Models available:"
echo "  --model tiny.en    Fastest, ~39MB  (lower accuracy)"
echo "  --model base.en    Balanced, ~74MB (default, recommended)"
echo "  --model small.en   Best, ~244MB    (highest accuracy)"
