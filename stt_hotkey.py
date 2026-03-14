#!/usr/bin/env python3
"""
linux-stt-hotkey - Lightweight push-to-talk speech-to-text for Linux.

Hold a hotkey to record speech, release to transcribe and type into
the active window. Uses faster-whisper (base.en) for offline English STT
with auto-punctuation and capitalization.

Usage:
    python3 stt_hotkey.py                          # default: Scroll Lock
    python3 stt_hotkey.py --hotkey f9               # use F9
    python3 stt_hotkey.py --hotkey super+shift+s    # key combo
    python3 stt_hotkey.py --model base.en           # model size (tiny.en/base.en/small.en)
    python3 stt_hotkey.py --no-type                 # print only, don't type
    python3 stt_hotkey.py --backend evdev            # force evdev backend

Dependencies:
    pip install faster-whisper sounddevice numpy pynput
    sudo apt install xdotool xclip libnotify-bin
"""

import argparse
import io
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from typing import Callable, Optional, Set

import numpy as np

logger = logging.getLogger("stt-hotkey")

# ───────────────────────────────────────────────────────────────────────────
# Audio Recorder
# ───────────────────────────────────────────────────────────────────────────

class MicRecorder:
    """Push-to-talk microphone capture using sounddevice."""

    def __init__(self, samplerate: int = 16000, channels: int = 1):
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "Missing Python dependency 'sounddevice'. "
                "Install requirements with: python3 -m pip install -r requirements.txt"
            ) from exc

        self._sd = sd
        self.samplerate = samplerate
        self.channels = channels
        self._audio_queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream = None
        self._recording = False

    def _callback(self, indata, frames, time_info, status):
        if status:
            logger.warning("Audio status: %s", status)
        if self._recording:
            self._audio_queue.put(indata.copy())

    def start(self):
        """Start capturing audio."""
        # Drain any old data
        while not self._audio_queue.empty():
            self._audio_queue.get_nowait()
        self._recording = True
        self._stream = self._sd.InputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            dtype="int16",
            blocksize=1024,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> bytes:
        """Stop capturing and return WAV bytes."""
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        frames = []
        while not self._audio_queue.empty():
            frames.append(self._audio_queue.get_nowait())

        if not frames:
            return b""

        audio = np.concatenate(frames, axis=0)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.samplerate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()


# ───────────────────────────────────────────────────────────────────────────
# Speech-to-Text (faster-whisper)
# ───────────────────────────────────────────────────────────────────────────

class SpeechRecognizer:
    """Offline English STT using faster-whisper. Auto-punctuates and capitalizes."""

    VALID_MODELS = ["tiny.en", "base.en", "small.en"]

    def __init__(self, model_name: Optional[str] = None):
        from faster_whisper import WhisperModel

        model = model_name or "base.en"
        if model not in self.VALID_MODELS:
            print(f"WARNING: Unknown model '{model}', using base.en")
            model = "base.en"

        logger.info("Loading faster-whisper model '%s' (downloads on first run)...", model)
        self._model = WhisperModel(model, device="cpu", compute_type="int8")
        logger.info("Model loaded")

    def transcribe(self, wav_bytes: bytes) -> str:
        """Transcribe WAV audio bytes to text with punctuation."""
        # Write to temp file (faster-whisper needs a file path or numpy array)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            tmp_path = f.name

        try:
            segments, info = self._model.transcribe(
                tmp_path,
                language="en",
                beam_size=3,
                vad_filter=True,         # skip silence
                vad_parameters=dict(
                    min_silence_duration_ms=300,
                ),
            )
            text = " ".join(seg.text.strip() for seg in segments)
            return text.strip()
        finally:
            os.unlink(tmp_path)


# ───────────────────────────────────────────────────────────────────────────
# Text Input (type/paste into active window)
# ───────────────────────────────────────────────────────────────────────────

def _detect_display_server() -> str:
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session == "wayland":
        return "wayland"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    return "x11"


def type_text(text: str):
    """Paste text into the currently focused window."""
    if not text:
        return

    server = _detect_display_server()

    if server == "wayland":
        _type_wayland(text)
    else:
        _type_x11(text)


def _type_x11(text: str):
    has_xclip = shutil.which("xclip")
    has_xdotool = shutil.which("xdotool")

    if has_xclip and has_xdotool:
        # Save clipboard
        try:
            old = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True, text=True, timeout=2,
            ).stdout
        except Exception:
            old = None

        # Set clipboard
        p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
        p.communicate(input=text.encode())

        # Paste
        time.sleep(0.05)
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], timeout=5)

        # Restore clipboard
        if old is not None:
            def restore():
                time.sleep(0.3)
                p2 = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
                p2.communicate(input=old.encode())
            threading.Thread(target=restore, daemon=True).start()

    elif has_xdotool:
        subprocess.run(["xdotool", "type", "--clearmodifiers", "--delay", "12", text], timeout=30)
    else:
        logger.error("Install xdotool and xclip for X11 typing support")


def _type_wayland(text: str):
    has_wlcopy = shutil.which("wl-copy")
    has_wtype = shutil.which("wtype")

    if has_wlcopy and has_wtype:
        p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
        p.communicate(input=text.encode())
        subprocess.run(["wtype", "-M", "ctrl", "-P", "v", "-p", "v", "-m", "ctrl"], timeout=5)
    elif has_wtype:
        subprocess.run(["wtype", text], timeout=30)
    else:
        logger.error("Install wtype and wl-clipboard for Wayland typing support")


# ───────────────────────────────────────────────────────────────────────────
# Desktop Notifications
# ───────────────────────────────────────────────────────────────────────────

def notify(title: str, body: str, icon: str = "audio-input-microphone", expire_ms: int = 3000):
    """Show a desktop notification."""
    if not shutil.which("notify-send"):
        return
    cmd = [
        "notify-send",
        f"--expire-time={expire_ms}",
        f"--icon={icon}",
        "--hint=string:x-canonical-private-synchronous:stt-hotkey",
        title,
        body,
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ───────────────────────────────────────────────────────────────────────────
# Global Hotkey Listener (pynput / evdev)
# ───────────────────────────────────────────────────────────────────────────

class HotkeyListener:
    """Base class for push-to-talk hotkey detection."""

    def __init__(self, on_press: Callable, on_release: Callable, hotkey: str):
        self.on_press = on_press
        self.on_release = on_release
        self.hotkey = hotkey.lower()
        self._stop_event = threading.Event()
        self._active = False
        self._error: Optional[Exception] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._stop_event.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run_wrapper, daemon=True)
        self._thread.start()
        time.sleep(0.2)
        if self._error is not None:
            raise self._error

    def stop(self):
        self._stop_event.set()
        self._cleanup()
        if self._thread:
            self._thread.join(timeout=2)

    def _run_wrapper(self):
        try:
            self._run()
        except Exception as exc:
            self._error = exc
            self._stop_event.set()

    def _run(self):
        raise NotImplementedError

    def _cleanup(self):
        pass

    def _handle_press(self):
        if not self._active:
            self._active = True
            self.on_press()

    def _handle_release(self):
        if self._active:
            self._active = False
            self.on_release()


class PynputListener(HotkeyListener):
    """X11 global hotkey using pynput (no root needed)."""

    def _parse_hotkey(self):
        from pynput.keyboard import Key, KeyCode

        MODIFIER_MAP = {
            "ctrl": Key.ctrl_l, "ctrl_l": Key.ctrl_l, "ctrl_r": Key.ctrl_r,
            "shift": Key.shift_l, "shift_l": Key.shift_l, "shift_r": Key.shift_r,
            "alt": Key.alt_l, "alt_l": Key.alt_l, "alt_r": Key.alt_r,
            "super": Key.cmd_l, "super_l": Key.cmd_l, "super_r": Key.cmd_r,
        }
        KEY_MAP = {
            "scroll_lock": Key.scroll_lock, "pause": Key.pause,
            "print_screen": Key.print_screen, "insert": Key.insert,
            "caps_lock": Key.caps_lock, "num_lock": Key.num_lock,
            "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
            "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
            "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
            "space": Key.space, "tab": Key.tab, "enter": Key.enter,
            "esc": Key.esc, "escape": Key.esc,
        }
        CANONICAL = {
            Key.ctrl_r: Key.ctrl_l, Key.shift_r: Key.shift_l,
            Key.alt_r: Key.alt_l, Key.cmd_r: Key.cmd_l,
        }

        parts = [p.strip() for p in self.hotkey.split("+")]
        modifiers: Set = set()
        trigger = None

        for part in parts:
            if part in MODIFIER_MAP:
                modifiers.add(MODIFIER_MAP[part])
            elif part in KEY_MAP:
                trigger = KEY_MAP[part]
            elif len(part) == 1:
                trigger = KeyCode.from_char(part)
            else:
                raise ValueError(f"Unknown key: {part!r}")

        if trigger is None:
            raise ValueError(f"No trigger key in hotkey: {self.hotkey!r}")

        return modifiers, trigger, CANONICAL

    def _run(self):
        from pynput.keyboard import Key, Listener

        required_mods, trigger, CANONICAL = self._parse_hotkey()
        held_mods: Set = set()

        ALL_MODS = {
            Key.ctrl_l, Key.ctrl_r, Key.shift_l, Key.shift_r,
            Key.alt_l, Key.alt_r, Key.cmd_l, Key.cmd_r,
        }

        def canon(k):
            return CANONICAL.get(k, k)

        def mods_ok():
            return {canon(m) for m in required_mods}.issubset({canon(m) for m in held_mods})

        def on_press(key):
            if key in ALL_MODS:
                held_mods.add(key)
            if key == trigger and mods_ok():
                self._handle_press()

        def on_release(key):
            if key in ALL_MODS:
                held_mods.discard(key)
            if key == trigger:
                self._handle_release()
            elif self._active and not mods_ok():
                self._handle_release()

        self._listener = Listener(on_press=on_press, on_release=on_release)
        self._listener.start()
        self._stop_event.wait()
        self._listener.stop()

    def _cleanup(self):
        if hasattr(self, "_listener"):
            self._listener.stop()


class EvdevListener(HotkeyListener):
    """Wayland/X11 global hotkey using evdev (needs input group)."""

    def _find_keyboards(self):
        import evdev
        devices = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities(verbose=False).get(1, [])
            except PermissionError as exc:
                raise RuntimeError(
                    "Cannot read keyboard devices via evdev. "
                    "Add your user to the input group, then log out and back in: "
                    "sudo usermod -aG input $USER"
                ) from exc
            if 30 in caps and len(caps) > 50:  # has KEY_A and many keys
                devices.append(dev)
        if not devices:
            raise RuntimeError(
                "No keyboard found. Add yourself to input group: "
                "sudo usermod -aG input $USER"
            )
        return devices

    def _parse_hotkey(self):
        import evdev.ecodes as e

        MODIFIER_MAP = {
            "ctrl": e.KEY_LEFTCTRL, "ctrl_l": e.KEY_LEFTCTRL, "ctrl_r": e.KEY_RIGHTCTRL,
            "shift": e.KEY_LEFTSHIFT, "shift_l": e.KEY_LEFTSHIFT, "shift_r": e.KEY_RIGHTSHIFT,
            "alt": e.KEY_LEFTALT, "alt_l": e.KEY_LEFTALT, "alt_r": e.KEY_RIGHTALT,
            "super": e.KEY_LEFTMETA, "super_l": e.KEY_LEFTMETA, "super_r": e.KEY_RIGHTMETA,
        }
        KEY_MAP = {
            "scroll_lock": e.KEY_SCROLLLOCK, "pause": e.KEY_PAUSE,
            "print_screen": e.KEY_SYSRQ, "insert": e.KEY_INSERT,
            "caps_lock": e.KEY_CAPSLOCK, "num_lock": e.KEY_NUMLOCK,
            "f1": e.KEY_F1, "f2": e.KEY_F2, "f3": e.KEY_F3, "f4": e.KEY_F4,
            "f5": e.KEY_F5, "f6": e.KEY_F6, "f7": e.KEY_F7, "f8": e.KEY_F8,
            "f9": e.KEY_F9, "f10": e.KEY_F10, "f11": e.KEY_F11, "f12": e.KEY_F12,
            "space": e.KEY_SPACE, "tab": e.KEY_TAB, "enter": e.KEY_ENTER,
            "esc": e.KEY_ESC, "escape": e.KEY_ESC,
        }
        for c in "abcdefghijklmnopqrstuvwxyz":
            KEY_MAP[c] = getattr(e, f"KEY_{c.upper()}")
        for n in "0123456789":
            KEY_MAP[n] = getattr(e, f"KEY_{n}")

        CANONICAL = {
            e.KEY_RIGHTCTRL: e.KEY_LEFTCTRL, e.KEY_RIGHTSHIFT: e.KEY_LEFTSHIFT,
            e.KEY_RIGHTALT: e.KEY_LEFTALT, e.KEY_RIGHTMETA: e.KEY_LEFTMETA,
        }

        parts = [p.strip() for p in self.hotkey.split("+")]
        modifiers = set()
        trigger = None

        for part in parts:
            if part in MODIFIER_MAP:
                modifiers.add(MODIFIER_MAP[part])
            elif part in KEY_MAP:
                trigger = KEY_MAP[part]
            else:
                raise ValueError(f"Unknown key: {part!r}")

        if trigger is None:
            raise ValueError(f"No trigger key in hotkey: {self.hotkey!r}")

        return modifiers, trigger, CANONICAL

    def _run(self):
        import select

        devices = self._find_keyboards()
        self._devices = devices
        modifiers, trigger, CANONICAL = self._parse_hotkey()
        held: Set[int] = set()

        def canon(c):
            return CANONICAL.get(c, c)

        def mods_ok():
            return modifiers.issubset({canon(c) for c in held})

        fd_map = {d.fd: d for d in devices}

        while not self._stop_event.is_set():
            r, _, _ = select.select(fd_map.keys(), [], [], 0.1)
            for fd in r:
                for event in fd_map[fd].read():
                    if event.type != 1:
                        continue
                    if event.value == 1:  # press
                        held.add(event.code)
                        if event.code == trigger and mods_ok():
                            self._handle_press()
                    elif event.value == 0:  # release
                        held.discard(event.code)
                        if event.code == trigger:
                            self._handle_release()
                        elif self._active and not mods_ok():
                            self._handle_release()

    def _cleanup(self):
        for dev in getattr(self, "_devices", []):
            try:
                dev.close()
            except Exception:
                pass


def create_listener(on_press, on_release, hotkey="scroll_lock", backend=None):
    """Auto-select the best hotkey backend."""
    if backend == "pynput":
        return PynputListener(on_press, on_release, hotkey)
    if backend == "evdev":
        return EvdevListener(on_press, on_release, hotkey)

    wayland = "WAYLAND_DISPLAY" in os.environ
    x11 = "DISPLAY" in os.environ

    if not wayland and x11:
        try:
            import pynput  # noqa: F401
            return PynputListener(on_press, on_release, hotkey)
        except ImportError:
            pass

    try:
        import evdev  # noqa: F401
        return EvdevListener(on_press, on_release, hotkey)
    except ImportError:
        pass

    try:
        import pynput  # noqa: F401
        return PynputListener(on_press, on_release, hotkey)
    except ImportError:
        pass

    raise RuntimeError("Install pynput or evdev: pip install pynput evdev")


def print_setup_help(error: Exception):
    server = _detect_display_server()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(script_dir, ".venv", "bin", "python")

    print(f"Error: {error}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Setup checklist:", file=sys.stderr)
    print("  1. Run the installer: ./install.sh", file=sys.stderr)
    print(f"     Or use the project venv directly: {venv_python} stt_hotkey.py", file=sys.stderr)

    if server == "wayland":
        print("  2. Install Wayland tools: sudo pacman -S wtype wl-clipboard", file=sys.stderr)
        print("  3. Enable global hotkeys: sudo usermod -aG input $USER", file=sys.stderr)
        print(f"  4. Log out and back in, then run: {venv_python} stt_hotkey.py --backend evdev --hotkey f9", file=sys.stderr)
    else:
        print("  2. Install X11 tools: sudo pacman -S xdotool xclip", file=sys.stderr)
        print(f"  3. Run: {venv_python} stt_hotkey.py --backend pynput --hotkey f9", file=sys.stderr)

    print("", file=sys.stderr)
    print(f"Detected session: {server}", file=sys.stderr)


# ───────────────────────────────────────────────────────────────────────────
# Main Application
# ───────────────────────────────────────────────────────────────────────────

class STTApp:
    """Ties everything together: hotkey -> record -> transcribe -> type."""

    def __init__(self, hotkey: str, model_path: Optional[str], backend: Optional[str],
                 auto_type: bool = True):
        self.auto_type = auto_type
        self._lock = threading.Lock()
        self._recording = False

        # Init components
        print("Loading speech recognition model...")
        self.recognizer = SpeechRecognizer(model_path)
        self.recorder = MicRecorder(samplerate=16000, channels=1)
        self.listener = create_listener(
            on_press=self._on_hotkey_press,
            on_release=self._on_hotkey_release,
            hotkey=hotkey,
            backend=backend,
        )

    def _on_hotkey_press(self):
        with self._lock:
            if self._recording:
                return
            self._recording = True
        logger.info("Recording started")
        notify("Recording", "Speak now...", expire_ms=30000)
        self.recorder.start()

    def _on_hotkey_release(self):
        with self._lock:
            if not self._recording:
                return
            self._recording = False

        logger.info("Recording stopped, transcribing...")
        notify("Processing", "Transcribing speech...", expire_ms=5000)

        wav_data = self.recorder.stop()
        if not wav_data:
            logger.warning("No audio captured")
            notify("No Audio", "No speech was recorded.", icon="dialog-warning", expire_ms=2000)
            return

        # Transcribe in a thread to avoid blocking the hotkey listener
        threading.Thread(target=self._transcribe, args=(wav_data,), daemon=True).start()

    def _transcribe(self, wav_data: bytes):
        text = self.recognizer.transcribe(wav_data)

        if not text:
            notify("No Speech", "Could not recognize any speech.",
                   icon="dialog-warning", expire_ms=2000)
            logger.info("No speech recognized")
            return

        logger.info("Transcribed: %s", text)
        print(f"  >> {text}")

        if self.auto_type:
            time.sleep(0.1)  # small delay for window focus
            type_text(text)

        preview = text[:80] + ("..." if len(text) > 80 else "")
        notify("Done", preview, icon="dialog-information", expire_ms=3000)

    def run(self):
        """Start the app and block until Ctrl+C."""
        self.listener.start()
        print("Ready! Hold your hotkey to record, release to transcribe.")
        print("Press Ctrl+C to quit.\n")

        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            print("\nShutting down...")
            self.listener.stop()


# ───────────────────────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Lightweight push-to-talk speech-to-text for Linux",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s                              # Scroll Lock push-to-talk
  %(prog)s --hotkey f9                  # F9 push-to-talk
  %(prog)s --hotkey super+shift+s       # key combo
  %(prog)s --no-type                    # print only, don't paste
  %(prog)s --backend evdev              # force evdev (Wayland)
""",
    )
    parser.add_argument(
        "--hotkey", default="scroll_lock",
        help="Hotkey for push-to-talk (default: scroll_lock). "
             "Examples: f9, pause, super+shift+s",
    )
    parser.add_argument(
        "--model", default="base.en",
        choices=["tiny.en", "base.en", "small.en"],
        help="Whisper model size (default: base.en). "
             "tiny.en=fastest/39MB, base.en=balanced/74MB, small.en=best/244MB",
    )
    parser.add_argument(
        "--backend", choices=["pynput", "evdev"], default=None,
        help="Force hotkey backend (default: auto-detect)",
    )
    parser.add_argument(
        "--no-type", action="store_true",
        help="Don't type text into active window, just print to terminal",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=== Linux STT Hotkey ===")
    print(f"  Model:     {args.model}")
    print(f"  Hotkey:    {args.hotkey}")
    print(f"  Auto-type: {'no' if args.no_type else 'yes'}")
    print()

    try:
        app = STTApp(
            hotkey=args.hotkey,
            model_path=args.model,
            backend=args.backend,
            auto_type=not args.no_type,
        )
        app.run()
    except (ImportError, ModuleNotFoundError, RuntimeError, OSError) as exc:
        print_setup_help(exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
