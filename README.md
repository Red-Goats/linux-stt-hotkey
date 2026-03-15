# linux-stt-hotkey

Offline push-to-talk speech-to-text for Linux.

Install it once, leave it running in the background, then hold a hotkey in any focused text field to dictate and release to paste the transcription.

- Fully **offline** — no internet after setup
- **Auto-punctuation and capitalization** (Whisper-based)
- Works on **X11** and **Wayland**
- English only

## What It Does

- Runs as a small background listener instead of taking over your terminal
- Pastes into the currently focused app, so it works in editors, browsers, chat apps, and terminals
- Uses local Whisper models with no cloud API dependency
- Installs a `stt-hotkey` launcher and autostart entry for easier daily use

## Requirements

- Linux desktop session on X11 or Wayland
- Python 3.9+
- A working microphone

```bash
chmod +x install.sh
./install.sh
```

The installer handles everything:
1. System packages (`portaudio`, `xdotool`, `xclip`, `wtype`, `wl-clipboard`, `libnotify`)
2. Creates a local virtualenv at `.venv/`
3. Installs Python packages (`faster-whisper`, `sounddevice`, `numpy`, `pynput`, `evdev`)
4. Downloads the Whisper `base.en` model (~74MB, one-time)
5. Installs a `stt-hotkey` launcher in `~/.local/bin/`
6. Adds desktop autostart so it is ready after login

## Quick Start

```bash
stt-hotkey
```

Then:
1. Focus any text box, editor, or terminal input.
2. Hold `F9` to record.
3. Release `F9` to transcribe and paste.

If `~/.local/bin` is not on your `PATH`, run:

```bash
~/.local/bin/stt-hotkey
```

Use these commands to manage it:

| Command | Description |
|---|---|
| `stt-hotkey` | Start in the background |
| `stt-hotkey run` | Run in the foreground |
| `stt-hotkey stop` | Stop the background app |
| `stt-hotkey status` | Show whether it is running |

## Options

| Command / Flag | Default | Description |
|---|---|---|
| `start` | yes | Start the app in the background |
| `run` | - | Run in the foreground |
| `stop` | - | Stop the background app |
| `status` | - | Show whether the background app is running |
| `--hotkey KEY` | `f9` | Key to hold for push-to-talk |
| `--model SIZE` | `base.en` | Whisper model size (see below) |
| `--backend BACKEND` | auto | Force `pynput` (X11) or `evdev` (Wayland) |
| `--no-type` | off | Print transcription only, don't paste |
| `-v, --verbose` | off | Enable debug logging |

## Examples

```bash
# Start in background with the default F9 hotkey
stt-hotkey

# Run in foreground for debugging
stt-hotkey run

# Use F8 as the hotkey
stt-hotkey start --hotkey f8

# Use the faster tiny model
stt-hotkey start --model tiny.en

# Just print to terminal, don't type into window
stt-hotkey run --no-type

# Force Wayland evdev backend
stt-hotkey start --backend evdev

# Stop the background app
stt-hotkey stop

# Check whether it is running
stt-hotkey status
```

## Hotkey Reference

Choose one single push-to-talk key:

| Name | Key |
|---|---|
| `f8` | Function key |
| `f9` | Function key |
| `f10` | Function key |
| `f11` | Function key |
| `f12` | Function key |
| `scroll_lock` | Scroll Lock |
| `pause` | Pause/Break |

---

## Model Sizes

| Model | Size | Speed | Accuracy | Best For |
|---|---|---|---|---|
| `tiny.en` | ~39MB | Fastest | Good | Low-end hardware |
| `base.en` | ~74MB | Fast | Better | Default, recommended |
| `small.en` | ~244MB | Moderate | Best | Maximum accuracy |

Models are downloaded automatically on first run and cached at `~/.cache/huggingface/`.

## Wayland

On Wayland, global hotkeys usually need the `evdev` backend and access to `/dev/input`:

```bash
# Add yourself to the input group
sudo usermod -aG input $USER

# Try a fresh shell with the new group, without logging out
newgrp input

# Arch packages used for paste/type on Wayland
sudo pacman -S wtype wl-clipboard

# Then run with evdev backend
stt-hotkey start --backend evdev
```

If `newgrp input` does not solve it, log out and back in once.

`f9` is the default hotkey because it is usually available without needing modifiers.

## Uninstall

```bash
chmod +x uninstall.sh
./uninstall.sh
```

Removes the local `.venv`, cached models, and optionally shared system packages.

## Troubleshooting

**No audio captured / microphone not found**
```bash
# List available audio devices
python3 -c "import sounddevice; print(sounddevice.query_devices())"
```
Make sure your microphone is not muted and is the default input device.

**Hotkey not detected (X11)**
```bash
pip install pynput
# Ensure DISPLAY is set:
echo $DISPLAY
```

**Hotkey not detected (Wayland)**
```bash
./install.sh
sudo usermod -aG input $USER
# Try a fresh shell first
newgrp input
# Then use --backend evdev
stt-hotkey start --backend evdev
```

**`xdotool` / `xclip` not found**
```bash
sudo pacman -S xdotool xclip
```

**Text not pasted (Wayland)**
```bash
sudo pacman -S wtype wl-clipboard
```

**Model download fails**
The model downloads from Hugging Face on first run. If you're behind a proxy:
```bash
export HTTPS_PROXY=http://your-proxy:port
stt-hotkey start
```
