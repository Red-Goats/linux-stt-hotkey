#!/bin/bash
# uninstall.sh - Remove linux-stt-hotkey local install artifacts
# Run: chmod +x uninstall.sh && ./uninstall.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}/hub"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/linux-stt-hotkey"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
LAUNCHER_PATH="$BIN_DIR/stt-hotkey"
DESKTOP_PATH="$APP_DIR/linux-stt-hotkey.desktop"
AUTOSTART_PATH="$AUTOSTART_DIR/linux-stt-hotkey.desktop"

echo "=== Linux Speech-to-Text Hotkey - Uninstaller ==="
echo ""

echo "[1/4] Stopping app and removing local launcher files..."
if [ -x "$VENV_DIR/bin/python" ]; then
    "$VENV_DIR/bin/python" "$SCRIPT_DIR/stt_hotkey.py" stop >/dev/null 2>&1 || true
fi

for PATH_TO_REMOVE in "$LAUNCHER_PATH" "$DESKTOP_PATH" "$AUTOSTART_PATH"; do
    if [ -e "$PATH_TO_REMOVE" ]; then
        rm -f "$PATH_TO_REMOVE"
        echo "  Removed: $PATH_TO_REMOVE"
    fi
done

if [ -d "$STATE_DIR" ]; then
    rm -rf "$STATE_DIR"
    echo "  Removed: $STATE_DIR"
fi

echo ""
echo "[2/4] Removing local virtualenv..."
if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
    echo "  Removed: $VENV_DIR"
else
    echo "  Not found: $VENV_DIR"
fi

echo ""
echo "[3/4] Removing cached Whisper models..."
REMOVED_ANY=false
for SIZE in tiny.en base.en small.en; do
    MODEL_DIR="$HF_CACHE/models--Systran--faster-whisper-${SIZE}"
    if [ -d "$MODEL_DIR" ]; then
        SIZE_HUMAN=$(du -sh "$MODEL_DIR" 2>/dev/null | cut -f1 || echo "?")
        rm -rf "$MODEL_DIR"
        echo "  Removed: faster-whisper-${SIZE} (${SIZE_HUMAN})"
        REMOVED_ANY=true
    fi
done

if [ "$REMOVED_ANY" = false ]; then
    echo "  No cached models found."
fi

echo ""
echo "[4/4] Optional shared packages..."
echo "  System packages may be used by other apps."
read -rp "  Remove shared system packages too? [y/N] " REMOVE_SYS

if [[ "$REMOVE_SYS" =~ ^[Yy]$ ]]; then
    if command -v apt-get &>/dev/null; then
        sudo apt-get remove -y --autoremove \
            xdotool xclip libnotify-bin libportaudio2 portaudio19-dev wtype wl-clipboard \
            2>/dev/null || true
    elif command -v dnf &>/dev/null; then
        sudo dnf remove -y \
            xdotool xclip libnotify portaudio portaudio-devel wtype wl-clipboard \
            2>/dev/null || true
    elif command -v pacman &>/dev/null; then
        sudo pacman -Rns --noconfirm \
            xdotool xclip libnotify portaudio wtype wl-clipboard \
            2>/dev/null || true
    else
        echo "  Unknown package manager; skipped system package removal."
    fi
else
    echo "  Kept shared system packages."
fi

echo ""
echo "=== Uninstall Complete ==="
echo ""
echo "Project files remain in:"
echo "  $SCRIPT_DIR"
