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
        libportaudio2 portaudio19-dev \
        xdotool xclip \
        libnotify-bin
elif command -v dnf &>/dev/null; then
    sudo dnf install -y \
        portaudio portaudio-devel \
        xdotool xclip \
        libnotify
elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm \
        portaudio \
        xdotool xclip \
        libnotify
else
    echo "WARNING: Unknown package manager. Install manually:"
    echo "  - portaudio (libportaudio2)"
    echo "  - xdotool, xclip"
    echo "  - libnotify (notify-send)"
fi

# Python dependencies
echo ""
echo "[2/3] Installing Python dependencies..."
pip install --quiet faster-whisper sounddevice numpy pynput

# Pre-download the model
echo ""
echo "[3/3] Pre-downloading Whisper base.en model (~80MB, one-time)..."
python3 -c "
from faster_whisper import WhisperModel
print('  Downloading and converting model...')
WhisperModel('base.en', device='cpu', compute_type='int8')
print('  Model cached successfully.')
"

# Make main script executable
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$SCRIPT_DIR/stt_hotkey.py"

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Usage:"
echo "  python3 $SCRIPT_DIR/stt_hotkey.py"
echo ""
echo "Default hotkey: Scroll Lock (push-to-talk)"
echo "Change with:    python3 $SCRIPT_DIR/stt_hotkey.py --hotkey super+shift+s"
echo ""
echo "Models available:"
echo "  --model tiny.en    Fastest, ~39MB  (lower accuracy)"
echo "  --model base.en    Balanced, ~74MB (default, recommended)"
echo "  --model small.en   Best, ~244MB    (highest accuracy)"
