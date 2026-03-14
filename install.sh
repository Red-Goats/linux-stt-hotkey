#!/bin/bash
# install.sh - Setup script for linux-stt-hotkey
# Run: chmod +x install.sh && ./install.sh

set -e

echo "=== Linux Speech-to-Text Hotkey - Installer ==="
echo ""

# System dependencies
echo "[1/3] Installing system dependencies..."
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
echo "[2/3] Installing Python dependencies..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"

# Pre-download the model
echo ""
echo "[3/3] Pre-downloading Whisper base.en model (~80MB, one-time)..."
"$VENV_DIR/bin/python" -c "
from faster_whisper import WhisperModel
print('  Downloading and converting model...')
WhisperModel('base.en', device='cpu', compute_type='int8')
print('  Model cached successfully.')
"

# Make main script executable
chmod +x "$SCRIPT_DIR/stt_hotkey.py"

echo ""
echo "=== Installation Complete ==="
echo ""
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    echo "Wayland session detected."
    echo "  For global hotkeys, add your user to the input group and log out/in:"
    echo "    sudo usermod -aG input \$USER"
    echo "  Recommended start command after re-login:"
    echo "    $VENV_DIR/bin/python $SCRIPT_DIR/stt_hotkey.py --backend evdev --hotkey f9"
    echo ""
fi

echo "Usage:"
echo "  $VENV_DIR/bin/python $SCRIPT_DIR/stt_hotkey.py"
echo ""
echo "Default hotkey: Scroll Lock (push-to-talk)"
echo "Change with:    $VENV_DIR/bin/python $SCRIPT_DIR/stt_hotkey.py --hotkey super+shift+s"
echo ""
echo "Models available:"
echo "  --model tiny.en    Fastest, ~39MB  (lower accuracy)"
echo "  --model base.en    Balanced, ~74MB (default, recommended)"
echo "  --model small.en   Best, ~244MB    (highest accuracy)"
