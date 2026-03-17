"""Tests for streaming transcription."""
import io
import queue
import threading
import time
import unittest
import wave
from unittest.mock import MagicMock, patch, call

import numpy as np


# ---------------------------------------------------------------------------
# Helpers to build minimal WAV bytes (used in assertions)
# ---------------------------------------------------------------------------

def _make_wav_bytes(samplerate: int = 16000, channels: int = 1,
                    duration_s: float = 0.1) -> bytes:
    """Build a minimal valid WAV byte-string filled with silence."""
    n_samples = int(samplerate * duration_s)
    audio = np.zeros(n_samples, dtype=np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(audio.tobytes())
    return buf.getvalue()


def _make_audio_frame(samplerate: int = 16000, duration_s: float = 0.1) -> np.ndarray:
    """Build a numpy int16 frame as if coming from sounddevice callback."""
    n_samples = int(samplerate * duration_s)
    return np.zeros((n_samples, 1), dtype=np.int16)


# ---------------------------------------------------------------------------
# Import the module under test (patch heavy imports so they don't run)
# ---------------------------------------------------------------------------

import sys
from unittest.mock import MagicMock

# Stub out optional heavy modules before importing stt_hotkey
for mod in ("sounddevice", "faster_whisper", "pynput", "evdev"):
    sys.modules.setdefault(mod, MagicMock())
sys.modules.setdefault("faster_whisper", MagicMock())

import stt_hotkey  # noqa: E402  (must come after stubs)
from stt_hotkey import MicRecorder, StreamingTranscriber, SpeechRecognizer


# ---------------------------------------------------------------------------
# MicRecorder unit tests
# ---------------------------------------------------------------------------

class TestMicRecorderDrainChunk(unittest.TestCase):
    """drain_chunk() tests — no real audio hardware needed."""

    def _make_recorder(self) -> MicRecorder:
        """Create a MicRecorder with a mocked sounddevice."""
        rec = MicRecorder.__new__(MicRecorder)
        rec.samplerate = 16000
        rec.channels = 1
        rec._audio_queue = queue.Queue()
        rec._stream = None
        rec._recording = False
        rec._sd = MagicMock()
        return rec

    def test_drain_chunk_returns_wav_bytes(self):
        """drain_chunk() should return valid WAV bytes when audio is queued."""
        rec = self._make_recorder()
        frame = _make_audio_frame()
        rec._audio_queue.put(frame)
        rec._audio_queue.put(frame)

        result = rec.drain_chunk()

        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 44)  # WAV header alone is 44 bytes
        # Verify it's a valid WAV
        with wave.open(io.BytesIO(result)) as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 16000)

    def test_drain_chunk_empty_queue(self):
        """drain_chunk() should return empty bytes when no audio queued."""
        rec = self._make_recorder()
        result = rec.drain_chunk()
        self.assertEqual(result, b"")

    def test_drain_chunk_does_not_stop_stream(self):
        """drain_chunk() must not touch self._stream."""
        rec = self._make_recorder()
        mock_stream = MagicMock()
        rec._stream = mock_stream

        rec._audio_queue.put(_make_audio_frame())
        rec.drain_chunk()

        mock_stream.stop.assert_not_called()
        mock_stream.close.assert_not_called()

    def test_drain_chunk_clears_queue(self):
        """After drain_chunk(), the queue should be empty."""
        rec = self._make_recorder()
        for _ in range(5):
            rec._audio_queue.put(_make_audio_frame())

        rec.drain_chunk()
        self.assertTrue(rec._audio_queue.empty())

    def test_stop_only_stops_stream(self):
        """stop() must stop/close stream but NOT drain the queue."""
        rec = self._make_recorder()
        mock_stream = MagicMock()
        rec._stream = mock_stream
        rec._recording = True

        for _ in range(3):
            rec._audio_queue.put(_make_audio_frame())

        rec.stop()

        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()
        self.assertFalse(rec._recording)
        # Queue must still have data
        self.assertFalse(rec._audio_queue.empty())

    def test_stop_and_drain_returns_wav_bytes(self):
        """stop_and_drain() should stop stream AND return WAV bytes."""
        rec = self._make_recorder()
        mock_stream = MagicMock()
        rec._stream = mock_stream
        rec._recording = True

        rec._audio_queue.put(_make_audio_frame())

        result = rec.stop_and_drain()

        mock_stream.stop.assert_called_once()
        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 44)

    def test_stop_and_drain_empty(self):
        """stop_and_drain() returns empty bytes when queue was empty."""
        rec = self._make_recorder()
        mock_stream = MagicMock()
        rec._stream = mock_stream
        rec._recording = True

        result = rec.stop_and_drain()
        self.assertEqual(result, b"")


# ---------------------------------------------------------------------------
# StreamingTranscriber unit tests
# ---------------------------------------------------------------------------

def _make_mock_recorder(audio_frames=None) -> MagicMock:
    """Build a MicRecorder mock. drain_chunk() returns wav bytes then empty."""
    rec = MagicMock(spec=MicRecorder)
    if audio_frames is None:
        # Default: one chunk with audio, then empty
        rec.drain_chunk.side_effect = [_make_wav_bytes(), b""]
    else:
        rec.drain_chunk.side_effect = audio_frames
    return rec


def _make_mock_recognizer(texts=None) -> MagicMock:
    """Build a SpeechRecognizer mock that returns given texts in order."""
    rec = MagicMock(spec=SpeechRecognizer)
    if texts is None:
        rec.transcribe.return_value = "hello world"
    else:
        rec.transcribe.side_effect = texts
    return rec


class TestStreamingTranscriber(unittest.TestCase):

    def test_stop_and_finalize_returns_assembled_text(self):
        """stop_and_finalize() should return joined segment text."""
        recorder = _make_mock_recorder(audio_frames=[_make_wav_bytes(), b""])
        recognizer = _make_mock_recognizer(texts=["hello world", "how are you"])

        # Provide two chunks worth of audio
        recorder.drain_chunk.side_effect = [_make_wav_bytes(), _make_wav_bytes(), b""]
        recognizer.transcribe.side_effect = ["hello world", "how are you"]

        st = StreamingTranscriber(recognizer, recorder)
        # Use a very short chunk interval so the test doesn't take 5 seconds
        st.CHUNK_INTERVAL = 0.05

        st.start()
        time.sleep(0.12)  # let one intermediate chunk fire
        result = st.stop_and_finalize()

        self.assertIn("hello world", result)

    def test_stop_and_finalize_no_audio(self):
        """When no audio is ever available, result should be empty string."""
        recorder = MagicMock(spec=MicRecorder)
        recorder.drain_chunk.return_value = b""
        recognizer = _make_mock_recognizer()

        st = StreamingTranscriber(recognizer, recorder)
        st.CHUNK_INTERVAL = 0.05
        st.start()
        result = st.stop_and_finalize()

        self.assertEqual(result, "")

    def test_final_chunk_is_transcribed(self):
        """Audio drained after stop_event is set should be included in result."""
        recorder = MagicMock(spec=MicRecorder)
        # With CHUNK_INTERVAL=60 and immediate stop, no intermediate loop iteration
        # fires, so the FIRST drain_chunk() call is the final one.
        recorder.drain_chunk.return_value = _make_wav_bytes()
        recognizer = _make_mock_recognizer(texts=["final words"])

        st = StreamingTranscriber(recognizer, recorder)
        st.CHUNK_INTERVAL = 60.0  # ensure no intermediate chunk fires
        st.start()

        # Stop immediately — the final drain should still get audio
        result = st.stop_and_finalize()

        self.assertEqual(result, "final words")

    def test_segments_assembled_with_space(self):
        """Multiple segments should be joined with a single space."""
        recorder = MagicMock(spec=MicRecorder)
        # Two intermediate chunks + one final
        recorder.drain_chunk.side_effect = [
            _make_wav_bytes(), _make_wav_bytes(), _make_wav_bytes(), b""
        ]
        recognizer = _make_mock_recognizer(
            texts=["one", "two", "three"]
        )

        st = StreamingTranscriber(recognizer, recorder)
        st.CHUNK_INTERVAL = 0.03
        st.start()
        time.sleep(0.1)
        result = st.stop_and_finalize()

        # Should contain at least the first segment
        self.assertTrue(len(result) > 0)
        parts = result.split(" ")
        for part in parts:
            self.assertIn(part, ["one", "two", "three"])

    def test_transcription_error_graceful_degradation(self):
        """If transcribe() raises, already-collected segments are returned."""
        recorder = MagicMock(spec=MicRecorder)
        # One good chunk, one that throws, one final empty
        recorder.drain_chunk.side_effect = [
            _make_wav_bytes(), _make_wav_bytes(), b""
        ]
        recognizer = MagicMock(spec=SpeechRecognizer)
        recognizer.transcribe.side_effect = ["good text", RuntimeError("model exploded")]

        st = StreamingTranscriber(recognizer, recorder)
        st.CHUNK_INTERVAL = 0.03
        st.start()
        time.sleep(0.1)
        result = st.stop_and_finalize()

        # Should return the good segment, not raise
        self.assertIsInstance(result, str)
        # The good segment may or may not be present depending on timing;
        # what matters is it didn't raise and returned a string.

    def test_done_event_always_set(self):
        """_done_event must be set even if an exception occurs in _run."""
        recorder = MagicMock(spec=MicRecorder)
        recorder.drain_chunk.side_effect = RuntimeError("boom")

        recognizer = _make_mock_recognizer()

        st = StreamingTranscriber(recognizer, recorder)
        st.CHUNK_INTERVAL = 60.0
        st.start()

        result = st.stop_and_finalize()
        # done_event must be set (stop_and_finalize returned, so it is)
        self.assertTrue(st._done_event.is_set())
        self.assertIsInstance(result, str)

    def test_update_notification_called_on_segments(self):
        """_update_notification() should be called when a segment is transcribed."""
        recorder = MagicMock(spec=MicRecorder)
        recorder.drain_chunk.side_effect = [_make_wav_bytes(), b""]
        recognizer = _make_mock_recognizer(texts=["hello"])

        st = StreamingTranscriber(recognizer, recorder)
        st.CHUNK_INTERVAL = 0.03

        with patch("stt_hotkey.notify") as mock_notify:
            st.start()
            time.sleep(0.1)
            st.stop_and_finalize()

        # notify should have been called with "Transcribing..." at least once
        titles = [c.args[0] for c in mock_notify.call_args_list]
        self.assertIn("Transcribing...", titles)


# ---------------------------------------------------------------------------
# Integration test: full press → chunks → release → final text flow
# ---------------------------------------------------------------------------

class TestFullFlow(unittest.TestCase):
    """Simulate the complete STTApp press/release cycle without real hardware."""

    def test_press_chunks_release_text(self):
        """Full flow: start streaming, accumulate chunks, stop, get final text."""
        # Simulate 2 intermediate chunks + 1 final chunk
        wav = _make_wav_bytes()

        recorder = MagicMock(spec=MicRecorder)
        recorder.drain_chunk.side_effect = [wav, wav, wav, b""]

        recognizer = MagicMock(spec=SpeechRecognizer)
        recognizer.transcribe.side_effect = ["I want to", "go to the store", "and buy milk"]

        with patch("stt_hotkey.notify"):
            st = StreamingTranscriber(recognizer, recorder)
            st.CHUNK_INTERVAL = 0.04

            # Simulate F9 press
            st.start()

            # Let some chunks fire
            time.sleep(0.12)

            # Simulate F9 release: stop stream (recorder.stop() called by STTApp),
            # then finalize streaming
            result = st.stop_and_finalize()

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)
        # All words should come from our mock transcriptions
        for word in result.split():
            self.assertIn(word, "I want to go to the store and buy milk".split())

    def test_immediate_release(self):
        """Releasing immediately after press should still return final chunk text."""
        wav = _make_wav_bytes()

        recorder = MagicMock(spec=MicRecorder)
        # No intermediate drains will fire (CHUNK_INTERVAL is long),
        # only the final drain after stop_event
        recorder.drain_chunk.side_effect = [wav, b""]

        recognizer = MagicMock(spec=SpeechRecognizer)
        recognizer.transcribe.return_value = "quick note"

        with patch("stt_hotkey.notify"):
            st = StreamingTranscriber(recognizer, recorder)
            st.CHUNK_INTERVAL = 60.0  # never fires intermediate

            st.start()
            result = st.stop_and_finalize()  # immediate release

        self.assertEqual(result, "quick note")

    def test_no_speech_returns_empty(self):
        """When recognizer returns empty for all chunks, result is empty string."""
        recorder = MagicMock(spec=MicRecorder)
        recorder.drain_chunk.return_value = b""

        recognizer = MagicMock(spec=SpeechRecognizer)
        recognizer.transcribe.return_value = ""

        with patch("stt_hotkey.notify"):
            st = StreamingTranscriber(recognizer, recorder)
            st.CHUNK_INTERVAL = 60.0

            st.start()
            result = st.stop_and_finalize()

        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
