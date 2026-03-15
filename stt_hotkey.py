#!/usr/bin/env python3
"""
linux-stt-hotkey - Lightweight push-to-talk speech-to-text for Linux.

Hold a hotkey to record speech, release to transcribe and type into
the active window. Uses faster-whisper (base.en) for offline English STT
with auto-punctuation and capitalization.

Usage:
    python3 stt_hotkey.py               # start in background
    python3 stt_hotkey.py run           # foreground mode
    python3 stt_hotkey.py --hotkey f8   # use F8
    python3 stt_hotkey.py stop
    python3 stt_hotkey.py status

Dependencies:
    pip install faster-whisper sounddevice numpy pynput
    sudo apt install xdotool xclip libnotify-bin
"""

import argparse
from dataclasses import dataclass
import fcntl
import io
import logging
import os
import queue
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger("stt-hotkey")

APP_NAME = "linux-stt-hotkey"
KNOWN_COMMANDS = {"run", "start", "stop", "status"}

SUPPORTED_HOTKEYS = (
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
    "scroll_lock",
    "pause",
)


@dataclass(frozen=True)
class RuntimeOptions:
    hotkey: str = "f9"
    model: str = "base.en"
    backend: Optional[str] = None
    auto_type: bool = True
    verbose: bool = False


@dataclass(frozen=True)
class AppPaths:
    state_dir: str
    pid_file: str
    log_file: str
    launcher_path: str


def _build_paths() -> AppPaths:
    state_home = os.environ.get("XDG_STATE_HOME")
    if not state_home:
        state_home = os.path.join(os.path.expanduser("~"), ".local", "state")

    state_dir = os.path.join(state_home, APP_NAME)
    os.makedirs(state_dir, exist_ok=True)

    bin_home = os.environ.get("XDG_BIN_HOME")
    if not bin_home:
        bin_home = os.path.join(os.path.expanduser("~"), ".local", "bin")

    return AppPaths(
        state_dir=state_dir,
        pid_file=os.path.join(state_dir, "app.pid"),
        log_file=os.path.join(state_dir, "app.log"),
        launcher_path=os.path.join(bin_home, "stt-hotkey"),
    )


PATHS = _build_paths()


def _read_pid() -> Optional[int]:
    try:
        with open(PATHS.pid_file, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
    except FileNotFoundError:
        return None

    if not value.isdigit():
        return None
    return int(value)


def _process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _remove_stale_pidfile():
    pid = _read_pid()
    if pid and not _process_running(pid):
        try:
            os.unlink(PATHS.pid_file)
        except FileNotFoundError:
            pass


def _is_running() -> bool:
    _remove_stale_pidfile()
    pid = _read_pid()
    return bool(pid and _process_running(pid))


def _build_run_command(args) -> list[str]:
    cmd = [sys.executable, os.path.abspath(__file__), "run"]
    cmd.extend(["--hotkey", args.hotkey])
    cmd.extend(["--model", args.model])
    if args.backend:
        cmd.extend(["--backend", args.backend])
    if args.no_type:
        cmd.append("--no-type")
    if args.verbose:
        cmd.append("--verbose")
    return cmd


def _options_from_args(args) -> RuntimeOptions:
    return RuntimeOptions(
        hotkey=getattr(args, "hotkey", "f9"),
        model=getattr(args, "model", "base.en"),
        backend=getattr(args, "backend", None),
        auto_type=not getattr(args, "no_type", False),
        verbose=getattr(args, "verbose", False),
    )


def _start_background(args) -> int:
    if _is_running():
        pid = _read_pid()
        print(f"{APP_NAME} is already running (pid {pid}).")
        return 0

    log_path = PATHS.log_file
    cmd = _build_run_command(args)
    with open(log_path, "ab") as log_handle:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
            close_fds=True,
        )

    for _ in range(30):
        time.sleep(0.2)
        if _is_running():
            pid = _read_pid()
            print(f"Started {APP_NAME} in the background (pid {pid}).")
            print(f"Log file: {log_path}")
            return 0

    print(f"Failed to start {APP_NAME}. Check log: {log_path}", file=sys.stderr)
    return 1


def _stop_background() -> int:
    pid = _read_pid()
    if not pid or not _process_running(pid):
        _remove_stale_pidfile()
        print(f"{APP_NAME} is not running.")
        return 0

    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        time.sleep(0.2)
        if not _process_running(pid):
            _remove_stale_pidfile()
            print(f"Stopped {APP_NAME}.")
            return 0

    print(f"Could not stop pid {pid}.", file=sys.stderr)
    return 1


def _print_status() -> int:
    if _is_running():
        print(f"{APP_NAME} is running (pid {_read_pid()}).")
        print(f"Log file: {PATHS.log_file}")
        return 0

    print(f"{APP_NAME} is not running.")
    return 1

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
        from pynput.keyboard import Key

        KEY_MAP = {
            "scroll_lock": Key.scroll_lock, "pause": Key.pause,
            "f8": Key.f8,
            "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
        }
        try:
            return KEY_MAP[self.hotkey]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported hotkey: {self.hotkey!r}. Choose one of: "
                f"{', '.join(SUPPORTED_HOTKEYS)}"
            ) from exc

    def _run(self):
        from pynput.keyboard import Listener

        trigger = self._parse_hotkey()

        def on_press(key):
            if key == trigger:
                self._handle_press()

        def on_release(key):
            if key == trigger:
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

        KEY_MAP = {
            "scroll_lock": e.KEY_SCROLLLOCK, "pause": e.KEY_PAUSE,
            "f8": e.KEY_F8,
            "f9": e.KEY_F9, "f10": e.KEY_F10, "f11": e.KEY_F11, "f12": e.KEY_F12,
        }
        try:
            return KEY_MAP[self.hotkey]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported hotkey: {self.hotkey!r}. Choose one of: "
                f"{', '.join(SUPPORTED_HOTKEYS)}"
            ) from exc

    def _run(self):
        import select

        devices = self._find_keyboards()
        self._devices = devices
        trigger = self._parse_hotkey()

        fd_map = {d.fd: d for d in devices}

        while not self._stop_event.is_set():
            r, _, _ = select.select(fd_map.keys(), [], [], 0.1)
            for fd in r:
                for event in fd_map[fd].read():
                    if event.type != 1:
                        continue
                    if event.value == 1:  # press
                        if event.code == trigger:
                            self._handle_press()
                    elif event.value == 0:  # release
                        if event.code == trigger:
                            self._handle_release()

    def _cleanup(self):
        for dev in getattr(self, "_devices", []):
            try:
                dev.close()
            except Exception:
                pass


def create_listener(on_press, on_release, hotkey="f9", backend=None):
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

    print(f"Error: {error}", file=sys.stderr)
    print("", file=sys.stderr)
    print("Setup checklist:", file=sys.stderr)
    print("  1. Run the installer: ./install.sh", file=sys.stderr)
    print(f"     Or use the launcher directly: {PATHS.launcher_path}", file=sys.stderr)

    if server == "wayland":
        print("  2. Install Wayland tools: sudo pacman -S wtype wl-clipboard", file=sys.stderr)
        print("  3. Enable global hotkeys: sudo usermod -aG input $USER", file=sys.stderr)
        print("  4. Try: newgrp input", file=sys.stderr)
        print(f"  5. Then run: {PATHS.launcher_path} start --backend evdev", file=sys.stderr)
        print("  6. If that still fails, log out and back in once.", file=sys.stderr)
    else:
        print("  2. Install X11 tools: sudo pacman -S xdotool xclip", file=sys.stderr)
        print(f"  3. Run: {PATHS.launcher_path} start --backend pynput", file=sys.stderr)

    print("", file=sys.stderr)
    print(f"Detected session: {server}", file=sys.stderr)


# ───────────────────────────────────────────────────────────────────────────
# Main Application
# ───────────────────────────────────────────────────────────────────────────

class STTApp:
    """Ties everything together: hotkey -> record -> transcribe -> type."""

    def __init__(self, options: RuntimeOptions):
        self.options = options
        self._lock = threading.Lock()
        self._recording = False
        self._pid_handle = None
        self._shutdown = threading.Event()

        # Init components
        print("Loading speech recognition model...")
        self.recognizer = SpeechRecognizer(options.model)
        self.recorder = MicRecorder(samplerate=16000, channels=1)
        self.listener = create_listener(
            on_press=self._on_hotkey_press,
            on_release=self._on_hotkey_release,
            hotkey=options.hotkey,
            backend=options.backend,
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

        if self.options.auto_type:
            time.sleep(0.1)  # small delay for window focus
            type_text(text)

        preview = text[:80] + ("..." if len(text) > 80 else "")
        notify("Done", preview, icon="dialog-information", expire_ms=3000)

    def run(self):
        """Start the app and block until Ctrl+C."""
        self._acquire_single_instance()
        previous_sigint = signal.getsignal(signal.SIGINT)
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def _handle_shutdown(signum, frame):
            self._shutdown.set()

        try:
            signal.signal(signal.SIGINT, _handle_shutdown)
            signal.signal(signal.SIGTERM, _handle_shutdown)
            self.listener.start()
            notify("STT Ready", "Hold the hotkey in any text field to dictate.", expire_ms=2500)
            print("Ready! Hold your hotkey to record, release to transcribe.")
            print("Press Ctrl+C to quit.\n")

            while not self._shutdown.is_set():
                time.sleep(0.5)
        except SystemExit:
            raise
        finally:
            print("\nShutting down...")
            self.listener.stop()
            signal.signal(signal.SIGINT, previous_sigint)
            signal.signal(signal.SIGTERM, previous_sigterm)
            self._release_single_instance()

    def _acquire_single_instance(self):
        pid_path = PATHS.pid_file
        self._pid_handle = open(pid_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._pid_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                f"{APP_NAME} is already running. Use 'stt-hotkey status' or "
                f"'stt-hotkey stop'."
            ) from exc

        self._pid_handle.seek(0)
        self._pid_handle.truncate()
        self._pid_handle.write(str(os.getpid()))
        self._pid_handle.flush()

    def _release_single_instance(self):
        if not self._pid_handle:
            return

        try:
            os.unlink(PATHS.pid_file)
        except FileNotFoundError:
            pass

        try:
            fcntl.flock(self._pid_handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._pid_handle.close()
        self._pid_handle = None


# ───────────────────────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lightweight push-to-talk speech-to-text for Linux",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s                              # start in background
  %(prog)s run                          # run in foreground
  %(prog)s --hotkey f8                  # use F8
  %(prog)s stop                         # stop the background app
  %(prog)s status                       # show whether it is running
""",
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run in the foreground")
    start_parser = subparsers.add_parser("start", help="Start in the background")
    subparsers.add_parser("stop", help="Stop the background app")
    subparsers.add_parser("status", help="Show background app status")

    for current in (run_parser, start_parser):
        current.add_argument(
            "--hotkey", default="f9", choices=SUPPORTED_HOTKEYS,
            help="Push-to-talk key (default: f9)",
        )
        current.add_argument(
            "--model", default="base.en",
            choices=["tiny.en", "base.en", "small.en"],
            help="Whisper model size (default: base.en). "
                 "tiny.en=fastest/39MB, base.en=balanced/74MB, small.en=best/244MB",
        )
        current.add_argument(
            "--backend", choices=["pynput", "evdev"], default=None,
            help="Force hotkey backend (default: auto-detect)",
        )
        current.add_argument(
            "--no-type", action="store_true",
            help="Don't type text into active window, just print to terminal",
        )
        current.add_argument(
            "-v", "--verbose", action="store_true",
            help="Enable debug logging",
        )
    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    if not argv:
        return ["start"]
    if argv[0] not in KNOWN_COMMANDS and argv[0] not in {"-h", "--help"}:
        return ["start", *argv]
    return argv


def _configure_logging(verbose: bool):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_runtime_banner(options: RuntimeOptions):
    print("=== Linux STT Hotkey ===")
    print(f"  Model:     {options.model}")
    print(f"  Hotkey:    {options.hotkey}")
    print(f"  Auto-type: {'yes' if options.auto_type else 'no'}")
    print()


def main():
    parser = _build_parser()
    argv = sys.argv[1:]
    argv = _normalize_argv(argv)

    args = parser.parse_args(argv)
    options = _options_from_args(args)
    _configure_logging(options.verbose)

    if args.command == "start":
        raise SystemExit(_start_background(args))
    if args.command == "stop":
        raise SystemExit(_stop_background())
    if args.command == "status":
        raise SystemExit(_print_status())

    _print_runtime_banner(options)

    try:
        app = STTApp(options)
        app.run()
    except (ImportError, ModuleNotFoundError, RuntimeError, OSError) as exc:
        print_setup_help(exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
