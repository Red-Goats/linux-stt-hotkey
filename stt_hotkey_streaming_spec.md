# STT Hotkey Streaming Transcription - Implementation Spec

## Overview

Add real-time streaming transcription to `stt_hotkey.py` so users see partial text appearing in a desktop notification while holding F9 (push-to-talk). On release, the final assembled text is pasted into the focused window.

## Current Architecture (Baseline)

```
F9 press → MicRecorder.start() → accumulate audio in queue
F9 release → MicRecorder.stop() → WAV bytes → SpeechRecognizer.transcribe() → type_text()
```

Key classes:
- `MicRecorder`: sounddevice InputStream, callback pushes `int16` numpy arrays to a queue. `stop()` concatenates all frames → WAV bytes.
- `SpeechRecognizer`: loads `faster-whisper` WhisperModel, `transcribe(wav_bytes)` writes temp file → runs model → returns text string.
- `STTApp`: orchestrator. `_on_hotkey_press()` starts recording. `_on_hotkey_release()` stops recording, spawns thread for `_transcribe()`.
- `notify()`: uses `notify-send` with `--hint=string:x-canonical-private-synchronous:stt-hotkey` (replaces previous notification in-place).
- `type_text()`: clipboard paste via xclip+xdotool (X11) or wl-copy+wtype (Wayland).

## Target Architecture (Streaming)

```
F9 press → MicRecorder.start() 
         → StreamingTranscriber thread starts
         → Every ~5s: drain audio chunk → transcribe chunk → update notification
         → Notification shows: "I want to go to the..." (transcribing...)
F9 release → MicRecorder signals stop
           → Final chunk transcribed
           → Full assembled text pasted into focused window
           → Notification dismissed (silent on success)
```

### Threading Model

```
Main Thread          Hotkey Thread         Streaming Thread
    |                    |                       |
    |  ← F9 press ───── |                       |
    |                    | ──── spawn ──────→    |
    |                    |                   [loop every 5s]
    |                    |                   drain_chunk()
    |                    |                   transcribe_chunk()
    |                    |                   update_notification()
    |                    |                       |
    |  ← F9 release ─── |                       |
    |                    | ── signal stop ──→    |
    |                    |                   transcribe_final()
    |                    |                   type_text(full)
    |                    |                   dismiss_notification()
```

## Detailed Changes

### 1. MicRecorder Modifications

**File:** `stt_hotkey.py`, class `MicRecorder`

Add a method to drain currently-buffered audio without stopping the stream:

```python
def drain_chunk(self) -> bytes:
    """Drain currently buffered audio as WAV bytes WITHOUT stopping the stream.
    Returns empty bytes if no audio buffered."""
    frames = []
    while not self._audio_queue.empty():
        try:
            frames.append(self._audio_queue.get_nowait())
        except queue.Empty:
            break
    
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
```

The existing `stop()` method remains unchanged (it still drains + stops the stream for the final chunk).

### 2. New Class: StreamingTranscriber

Add this new class after `SpeechRecognizer`:

```python
class StreamingTranscriber:
    """Manages streaming transcription: periodic chunk processing + notification updates."""
    
    CHUNK_INTERVAL = 5.0  # seconds between chunk transcriptions
    
    def __init__(self, recognizer: SpeechRecognizer, recorder: MicRecorder):
        self._recognizer = recognizer
        self._recorder = recorder
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._segments: list[str] = []  # accumulated transcribed segments
        self._lock = threading.Lock()
        self._final_text: Optional[str] = None
        self._done_event = threading.Event()  # signals transcription complete
    
    def start(self):
        """Start the streaming transcription loop in a background thread."""
        self._stop_event.clear()
        self._done_event.clear()
        self._segments = []
        self._final_text = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def stop_and_finalize(self) -> str:
        """Signal stop, process final chunk, return full assembled text.
        Called from hotkey release handler. Blocks until final text ready."""
        self._stop_event.set()
        # Wait for streaming thread to finish (max 30s for final transcription)
        self._done_event.wait(timeout=30.0)
        return self._final_text or ""
    
    def _run(self):
        """Main streaming loop. Runs in background thread."""
        try:
            while not self._stop_event.is_set():
                # Wait for chunk interval or stop signal
                self._stop_event.wait(timeout=self.CHUNK_INTERVAL)
                
                if self._stop_event.is_set():
                    break  # will handle final chunk below
                
                # Drain and transcribe intermediate chunk
                wav_data = self._recorder.drain_chunk()
                if wav_data:
                    text = self._recognizer.transcribe(wav_data)
                    if text:
                        with self._lock:
                            self._segments.append(text)
                        self._update_notification()
            
            # Final chunk: drain remaining audio from recorder
            # Note: recorder.stop() is called by the caller (STTApp._on_hotkey_release)
            # BEFORE stop_and_finalize(), so we use drain_chunk() for any remaining data
            final_wav = self._recorder.drain_chunk()
            if final_wav:
                text = self._recognizer.transcribe(final_wav)
                if text:
                    with self._lock:
                        self._segments.append(text)
            
            with self._lock:
                self._final_text = " ".join(self._segments)
            
        except Exception:
            logger.exception("Streaming transcription error")
            with self._lock:
                self._final_text = " ".join(self._segments)  # return what we have
        finally:
            self._done_event.set()
    
    def _update_notification(self):
        """Update the desktop notification with current partial text."""
        with self._lock:
            partial = " ".join(self._segments)
        
        if not partial:
            return
        
        # Truncate for notification display
        display = partial[:120] + ("..." if len(partial) > 120 else "")
        notify(
            "Transcribing...",
            display,
            icon="audio-input-microphone",
            expire_ms=30000,  # long expiry, will be replaced
        )
```

### 3. STTApp Modifications

Replace the current press/release flow with streaming-aware logic:

```python
class STTApp:
    def __init__(self, options: RuntimeOptions):
        # ... existing init ...
        self._streaming: Optional[StreamingTranscriber] = None

    def _on_hotkey_press(self):
        with self._lock:
            if self._recording:
                return
            self._recording = True
        
        logger.info("Recording started (streaming mode)")
        notify("Recording", "Speak now...", expire_ms=30000)
        self.recorder.start()
        
        # Start streaming transcription
        self._streaming = StreamingTranscriber(self.recognizer, self.recorder)
        self._streaming.start()

    def _on_hotkey_release(self):
        with self._lock:
            if not self._recording:
                return
            self._recording = False
        
        logger.info("Recording stopped, finalizing...")
        
        # Stop the audio stream first so drain_chunk gets everything
        self.recorder.stop()
        
        # Finalize in a thread to avoid blocking hotkey listener
        streaming = self._streaming
        self._streaming = None
        threading.Thread(
            target=self._finalize_streaming,
            args=(streaming,),
            daemon=True,
        ).start()

    def _finalize_streaming(self, streaming: StreamingTranscriber):
        """Finalize streaming transcription and type the result."""
        if streaming is None:
            return
        
        notify("Processing", "Finalizing...", expire_ms=5000)
        text = streaming.stop_and_finalize()
        
        if not text:
            notify("No Speech", "Could not recognize any speech.",
                   icon="dialog-warning", expire_ms=2000)
            logger.info("No speech recognized")
            return
        
        logger.info("Transcribed: %s", text)
        print(f"  >> {text}")
        
        if self.options.auto_type:
            time.sleep(0.1)
            type_text(text)
        
        # Silent on success - just dismiss the notification
        notify("Done", text[:80] + ("..." if len(text) > 80 else ""),
               icon="dialog-information", expire_ms=2000)
```

### 4. Important: MicRecorder.stop() Change

The current `stop()` drains the queue AND stops the stream. For streaming, we need `stop()` to be called **before** `stop_and_finalize()` so the final `drain_chunk()` inside the streaming thread gets the remaining audio. 

**However**, there's a race condition: `stop()` drains the queue, so `drain_chunk()` in the streaming thread would get nothing.

**Fix:** Modify `MicRecorder.stop()` to only stop the stream, NOT drain. Add `stop_and_drain()` for the non-streaming path (backward compat):

```python
class MicRecorder:
    def stop(self):
        """Stop the audio stream. Does NOT drain the queue."""
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
    
    def stop_and_drain(self) -> bytes:
        """Stop the stream and return all buffered audio as WAV bytes.
        This is the original stop() behavior, kept for non-streaming use."""
        self.stop()
        return self.drain_chunk()
```

Then `StreamingTranscriber._run()` final section becomes:
```python
# After stop_event is set, recorder.stop() has been called by STTApp
# drain_chunk() gets whatever audio is left in the queue
final_wav = self._recorder.drain_chunk()
```

### 5. Notification Behavior

- **On press:** "Recording - Speak now..."
- **During recording (every 5s):** "Transcribing... - I want to go to the store and..." (replaces in-place via `x-canonical-private-synchronous` hint)
- **On release processing:** "Processing - Finalizing..."  
- **On success:** "Done - [preview]" (2s expiry, effectively silent)
- **On no speech:** "No Speech - Could not recognize any speech." (2s, warning icon)
- **On error:** Log error, could add Discord alert hook later

### 6. Error Handling

In `StreamingTranscriber._run()`:
- Catch all exceptions, log them
- Return whatever segments were successfully transcribed (graceful degradation)
- If the model crashes on a chunk, skip that chunk and continue

### 7. Files to Modify

Only one file needs changes: **`stt_hotkey.py`**

Changes summary:
1. `MicRecorder`: Add `drain_chunk()`, rename `stop()` → split into `stop()` + `stop_and_drain()`
2. Add `StreamingTranscriber` class (new, ~80 lines)
3. `STTApp.__init__`: Add `self._streaming` attribute
4. `STTApp._on_hotkey_press`: Add streaming start
5. `STTApp._on_hotkey_release`: Replace with streaming finalization
6. `STTApp._transcribe`: Remove (replaced by `_finalize_streaming`)
7. Add `STTApp._finalize_streaming` method

### 8. Testing

Create `test_streaming.py` with:

```python
"""Tests for streaming transcription."""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

# Test 1: MicRecorder.drain_chunk() returns audio without stopping stream
# Test 2: MicRecorder.drain_chunk() returns empty bytes when no audio
# Test 3: StreamingTranscriber processes chunks at interval
# Test 4: StreamingTranscriber.stop_and_finalize() returns assembled text
# Test 5: StreamingTranscriber handles transcription errors gracefully
# Test 6: Full integration: press → chunks → release → final text
```

Implement these tests with mocked `SpeechRecognizer` and `MicRecorder` to avoid needing actual audio hardware.

### 9. What NOT to Change

- CLI interface (no new flags needed)
- Hotkey listener system (pynput/evdev)
- Display server detection
- Text injection (type_text)
- Process management (start/stop/status/pidfile)
- The `notify()` function itself (it already supports in-place replacement)

### 10. Dependency Changes

None. All dependencies already exist:
- `threading`, `queue` (stdlib)
- `notify-send` (already required)
- `faster-whisper` (already required)

## Implementation Order

1. Add `MicRecorder.drain_chunk()` and refactor `stop()`
2. Add `StreamingTranscriber` class
3. Modify `STTApp` to use streaming
4. Write tests
5. Manual test on actual hardware (hold F9, watch notifications update)
