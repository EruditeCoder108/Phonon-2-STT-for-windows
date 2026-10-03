"""
Phonon-2 Speech Engine Manager

Manages the local Phonon-2 server daemon (fermion serve) and provides:
1. One-Shot Audio Transcription (Fast HTTP POST for push-to-talk burst dictation)
2. Real-Time Streaming Transcription (WebSocket ws://.../v1/audio/stream for live progressive text)
"""

import subprocess
import threading
import time
import json
import queue
import logging
import urllib.request
import urllib.error
import io
import wave
import sys
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000


class StreamingSession:
    """Manages a single WebSocket streaming transcription session.

    The session runs a polling loop in a dedicated thread:
    - Drains queued audio chunks and sends them over the WebSocket
    - Receives partial/final transcription results from the server
    - Handles clean shutdown with end-of-stream signaling
    """

    def __init__(
        self,
        ws_url: str,
        on_partial: Callable[[str], None],
        on_final: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
    ):
        self._ws_url = ws_url
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_done = on_done
        self._on_error = on_error
        self._audio_queue: queue.Queue = queue.Queue(maxsize=500)
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self):
        """Opens the WebSocket and begins the streaming session in a background thread."""
        if self._active:
            return
        self._active = True
        threading.Thread(target=self._run, daemon=True, name="StreamSession").start()

    def send_audio(self, chunk: bytes):
        """Queue an audio chunk to send. Safe to call from the audio callback thread."""
        if not self._active:
            return
        try:
            self._audio_queue.put_nowait(chunk)
        except queue.Full:
            pass  # Drop frame rather than block the real-time audio thread

    def stop(self):
        """Signal the session to finish. Remaining server responses are drained."""
        if not self._active:
            return
        self._active = False
        try:
            self._audio_queue.put_nowait(None)  # Sentinel to break send loop
        except queue.Full:
            pass

    def _process_message(self, raw: str):
        """Parse and dispatch a single server JSON message."""
        data = json.loads(raw)
        msg_type = data.get("type", "")
        text = data.get("text", "").strip()

        if msg_type == "partial":
            self._on_partial(text)
        elif msg_type == "final":
            if text:
                self._on_final(text)
        elif msg_type == "done":
            return "done"
        elif msg_type == "error":
            self._on_error(data.get("message", "Unknown server error"))
            return "error"
        return "ok"

    def _run(self):
        from websockets.sync.client import connect

        ws = None
        try:
            ws = connect(self._ws_url, close_timeout=5)

            # Opening handshake: tell server our audio format
            ws.send(json.dumps({
                "sample_rate": 16000,
                "format": "pcm_s16le",
            }))
            logger.info("Streaming session opened.")

            # ── Main send+receive polling loop ──
            sending = True
            while sending:
                # Send: drain queued audio
                while not self._audio_queue.empty():
                    try:
                        chunk = self._audio_queue.get_nowait()
                    except queue.Empty:
                        break
                    if chunk is None:
                        sending = False
                        break
                    ws.send(chunk)

                # Receive: non-blocking check for server messages
                try:
                    raw = ws.recv(timeout=0.03)
                    result = self._process_message(raw)
                    if result in ("done", "error"):
                        sending = False
                except TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"WebSocket receive error: {e}")
                    sending = False

            # ── Drain phase: collect remaining finals after audio ends ──
            logger.info("Audio stream ended, draining remaining results...")
            drain_deadline = time.time() + 5.0
            while time.time() < drain_deadline:
                try:
                    raw = ws.recv(timeout=0.5)
                    result = self._process_message(raw)
                    if result in ("done", "error"):
                        break
                except TimeoutError:
                    break
                except Exception:
                    break

        except Exception as e:
            self._on_error(f"Streaming session error: {e}")
            logger.error(f"Streaming session error: {e}", exc_info=True)
        finally:
            self._active = False
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass
            logger.info("Streaming session closed.")
            try:
                self._on_done()
            except Exception:
                pass


class PhononEngine:
    def __init__(self, port: int = 8010, host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self.base_url = f"http://{self.host}:{self.port}"
        self.ws_url = f"ws://{self.host}:{self.port}/v1/audio/stream"
        self._process: Optional[subprocess.Popen] = None
        self._is_ready = False
        self._lock = threading.Lock()

    def is_server_running(self) -> bool:
        """Checks if the local Phonon server is responsive."""
        try:
            req = urllib.request.Request(f"{self.base_url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _kill_stale_servers(self):
        """Kill any orphaned phonon-2.exe processes from previous runs."""
        try:
            result = subprocess.run(
                ["taskkill", "/f", "/im", "phonon-2.exe"],
                capture_output=True, creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                logger.info("Killed stale phonon-2.exe process(es).")
                time.sleep(1)
        except Exception:
            pass

    def start_server(self, wait_timeout: int = 120):
        """Starts the local Phonon-2 server in the background if not already running."""
        with self._lock:
            # Always kill stale servers from previous crashed sessions
            self._kill_stale_servers()

            # Locate phonon-2.exe or fermion.exe in the venv
            venv_bin_dir = os.path.dirname(sys.executable)
            phonon_exe = os.path.join(venv_bin_dir, "phonon-2.exe")
            if not os.path.exists(phonon_exe):
                phonon_exe = os.path.join(venv_bin_dir, "fermion.exe")

            cmd = [phonon_exe, "serve", "--port", str(self.port)]
            logger.info(f"Starting Phonon-2 server: {' '.join(cmd)}")

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )

            # Wait for /health to become available (poll every 2s)
            start_time = time.time()
            while time.time() - start_time < wait_timeout:
                if self._process.poll() is not None:
                    stderr_out = self._process.stderr.read().decode(errors="replace") if self._process.stderr else ""
                    raise RuntimeError(f"Phonon-2 server exited with code {self._process.returncode}. Stderr: {stderr_out[:500]}")
                if self.is_server_running():
                    logger.info("Phonon-2 server is ready and healthy.")
                    self._is_ready = True
                    return
                time.sleep(2.0)

            raise TimeoutError(f"Phonon-2 server failed to start within {wait_timeout}s.")

    def stop_server(self):
        """Terminates the local server process if started by this instance."""
        with self._lock:
            if self._process:
                logger.info("Stopping Phonon-2 server process...")
                try:
                    self._process.terminate()
                    self._process.wait(timeout=3)
                except Exception:
                    self._process.kill()
                self._process = None
            self._is_ready = False

    def create_streaming_session(
        self,
        on_partial: Callable[[str], None],
        on_final: Callable[[str], None],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> StreamingSession:
        """Creates and starts a new WebSocket streaming transcription session."""
        session = StreamingSession(
            ws_url=self.ws_url,
            on_partial=on_partial,
            on_final=on_final,
            on_done=on_done,
            on_error=on_error,
        )
        session.start()
        return session

    def transcribe_wav_bytes(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        """
        Takes raw 16-bit mono PCM bytes, wraps them into WAV format,
        and posts to /v1/audio/transcriptions.
        Returns the recognized text (batch/fallback mode).
        """
        if not pcm_bytes:
            return ""

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)

        wav_data = wav_buffer.getvalue()

        boundary = "---------------------------PhononBoundary123456"
        crlf = "\r\n"
        body = bytearray()
        body.extend(f"--{boundary}{crlf}".encode())
        body.extend(f'Content-Disposition: form-data; name="file"; filename="dictation.wav"{crlf}'.encode())
        body.extend(f"Content-Type: audio/wav{crlf}{crlf}".encode())
        body.extend(wav_data)
        body.extend(crlf.encode())
        body.extend(f"--{boundary}--{crlf}".encode())

        req = urllib.request.Request(
            f"{self.base_url}/v1/audio/transcriptions",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
                "Connection": "close",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_json = json.loads(resp.read().decode())
                return resp_json.get("text", "").strip()
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode()
            logger.error(f"HTTP error during transcription ({e.code}): {err_msg}")
            return ""
        except Exception as e:
            logger.error(f"Transcription error: {e}", exc_info=True)
            return ""
