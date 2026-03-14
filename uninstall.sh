#!/bin/bash
# uninstall.sh - Remove linux-stt-hotkey and its dependencies
# Run: chmod +x uninstall.sh && ./uninstall.sh

set -e

echo "=== Linux Speech-to-Text Hotkey - Uninstaller ==="
echo ""

# ── 1. Python packages ──────────────────────────────────────────────────────
echo "[1/3] Removing Python packages..."

PACKAGES=(faster-whisper sounddevice pynput evdev)
OPTIONAL_PACKAGES=(numpy)

for pkg in "${PACKAGES[@]}"; do
    if pip show "$pkg" &>/dev/null 2>&1; then
        pip uninstall -y "$pkg" --quiet
        echo "  Removed: $pkg"
    else
        echo "  Skipped (not installed): $pkg"
    fi
done

# numpy is a common dep — warn before removing
echo ""
echo "  numpy is a common dependency used by many other tools."
read -rp "  Remove numpy too? [y/N] " REMOVE_NUMPY
if [[ "$REMOVE_NUMPY" =~ ^[Yy]$ ]]; then
    if pip show numpy &>/dev/null 2>&1; then
        pip uninstall -y numpy --quiet
        echo "  Removed: numpy"
    else
        echo "  Skipped (not installed): numpy"
    fi
else
    echo "  Kept: numpy"
fi

# ── 2. Cached Whisper models ────────────────────────────────────────────────
echo ""
echo "[2/3] Removing cached Whisper models..."

HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}/hub"
REMOVED_ANY=false

for SIZE in tiny.en base.en small.en; do
    # faster-whisper uses Systran's converted models on HuggingFace
    MODEL_DIR="$HF_CACHE/models--Systran--faster-whisper-${SIZE}"
    if [ -d "$MODEL_DIR" ]; then
        SIZE_HUMAN=$(du -sh "$MODEL_DIR" 2>/dev/null | cut -f1 || echo "?")
        rm -rf "$MODEL_DIR"
        echo "  Removed: faster-whisper-${SIZE} (${SIZE_HUMAN})"
        REMOVED_ANY=true
    fi
done

if [ "$REMOVED_ANY" = false ]; then
    echo "  No cached models found — nothing to remove."
fi

# ── 3. System packages (opt-in) ─────────────────────────────────────────────
echo ""
echo "[3/3] System packages (xdotool, xclip, libnotify-bin, portaudio)."
echo "  WARNING: These may be used by other applications."
read -rp "  Remove system packages? [y/N] " REMOVE_SYS

if [[ "$REMOVE_SYS" =~ ^[Yy]$ ]]; then
    if command -v apt-get &>/dev/null; then
        sudo apt-get remove -y --autoremove \
            xdotool xclip libnotify-bin libportaudio2 portaudio19-dev \
            2>/dev/null || true
    elif command -v dnf &>/dev/null; then
        sudo dnf remove -y \
            xdotool xclip libnotify portaudio portaudio-devel \
            2>/dev/null || true
    elif command -v pacman &>/dev/null; then
        sudo pacman -Rns --noconfirm \
            xdotool xclip libnotify portaudio \
            2>/dev/null || true
    else
        echo "  Unknown package manager — skipping system packages."
    fi
    echo "  System packages removed."
else
    echo "  Kept system packages."
fi

# ── Done ────────────────────────────────────────────────────────────────────
echo ""
echo "=== Uninstall Complete ==="
echo ""
echo "The following were NOT removed (delete manually if desired):"
echo "  - Local virtualenv: $(cd "$(dirname "$0")" && pwd)/.venv"
echo "  - This project directory ($(cd "$(dirname "$0")" && pwd))"
echo "  - Any Wayland tools: wtype, wl-clipboard"
