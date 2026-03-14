# linux-stt-hotkey

Lightweight push-to-talk speech-to-text for Linux. Hold a hotkey to record, release to transcribe — text is pasted directly into the active window.

- Fully **offline** — no internet after setup
- **Auto-punctuation and capitalization** (Whisper-based)
- Works on **X11** and **Wayland**
- English only

---

## Requirements

- Linux (Ubuntu 20.04+, Fedora 36+, Arch, etc.)
- Python 3.9+
- X11 or Wayland desktop session

---

## Installation

```bash
chmod +x install.sh
./install.sh
```

The installer handles everything:
1. System packages (`portaudio`, `xdotool`, `xclip`, `wtype`, `wl-clipboard`, `libnotify`)
2. Creates a local virtualenv at `.venv/`
3. Installs Python packages (`faster-whisper`, `sounddevice`, `numpy`, `pynput`, `evdev`)
4. Downloads the Whisper `base.en` model (~74MB, one-time)

---

## Usage

```bash
.venv/bin/python stt_hotkey.py
```

**Hold** your hotkey to start recording. **Release** to transcribe and paste.

### Options

| Flag | Default | Description |
|---|---|---|
| `--hotkey KEY` | `scroll_lock` | Key to hold for push-to-talk |
| `--model SIZE` | `base.en` | Whisper model size (see below) |
| `--backend BACKEND` | auto | Force `pynput` (X11) or `evdev` (Wayland) |
| `--no-type` | off | Print transcription only, don't paste |
| `-v, --verbose` | off | Enable debug logging |

### Examples

```bash
# Default: Scroll Lock hotkey, base.en model
.venv/bin/python stt_hotkey.py

# Use F9 as the hotkey
.venv/bin/python stt_hotkey.py --hotkey f9

# Key combo
.venv/bin/python stt_hotkey.py --hotkey super+shift+s

# Use the faster tiny model
.venv/bin/python stt_hotkey.py --model tiny.en

# Just print to terminal, don't type into window
.venv/bin/python stt_hotkey.py --no-type

# Force Wayland evdev backend
.venv/bin/python stt_hotkey.py --backend evdev
```

---

## Hotkey Reference

### Modifier keys (combine with `+`)

| Name | Key |
|---|---|
| `ctrl` | Left/Right Ctrl |
| `shift` | Left/Right Shift |
| `alt` | Left/Right Alt |
| `super` | Super / Windows key |

### Trigger keys

| Name | Key |
|---|---|
| `scroll_lock` | Scroll Lock |
| `pause` | Pause/Break |
| `f1` – `f12` | Function keys |
| `insert`, `home`, `end` | Navigation |
| `page_up`, `page_down` | Navigation |
| `caps_lock`, `num_lock` | Lock keys |
| `esc` / `escape` | Escape |
| `space`, `tab`, `enter` | Common keys |
| `a`–`z`, `0`–`9` | Characters |

**Combo example:** `--hotkey ctrl+shift+f9`

---

## Model Sizes

| Model | Size | Speed | Accuracy | Best For |
|---|---|---|---|---|
| `tiny.en` | ~39MB | Fastest | Good | Low-end hardware |
| `base.en` | ~74MB | Fast | Better | Default, recommended |
| `small.en` | ~244MB | Moderate | Best | Maximum accuracy |

Models are downloaded automatically on first run and cached at `~/.cache/huggingface/`.

---

## Wayland

On Wayland, use the `evdev` backend for global hotkeys. This needs access to `/dev/input`:

```bash
# Add yourself to the input group (one-time, requires re-login)
sudo usermod -aG input $USER

# Arch packages used for paste/type on Wayland
sudo pacman -S wtype wl-clipboard

# Then run with evdev backend
.venv/bin/python stt_hotkey.py --backend evdev --hotkey f9
```

`scroll_lock` is the default hotkey, but `f9` is a safer default on many laptops and compact keyboards.

---

## Uninstall

```bash
chmod +x uninstall.sh
./uninstall.sh
```

Removes Python packages, cached models, and optionally system packages.

---

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
.venv/bin/python -m pip install -r requirements.txt
sudo usermod -aG input $USER
# Log out and back in, then use --backend evdev
.venv/bin/python stt_hotkey.py --backend evdev --hotkey f9
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
.venv/bin/python stt_hotkey.py
```
