"""
Phonon Speech Engine Manager

Owns the selected local Phonon server (`fermion serve MODEL`) and talks to it over HTTP:

  * Process hygiene — the server's stdout/stderr are DRAINED continuously. The server logs one
    stderr line per request; an unread pipe fills after ~4 KB (~60 requests) and then blocks the
    server mid-request, which looks like "transcription just stopped working".
  * The server is placed in a Windows Job Object with KILL_ON_JOB_CLOSE, so it dies with this
    process even on a hard crash (no orphaned 1.4 GB processes).
  * A watchdog polls /health, restarts the server if it dies or wedges, and reports state changes.
  * transcribe() returns text plus per-word timestamps (verbose_json), which the pipeline uses to
    stitch phrases together and to sanity-check words.
"""

import ctypes
import collections
import http.client
import io
import importlib.metadata
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable, Deque, List, Optional

from src.config import DEFAULT_PHONON_MODEL, PHONON_MODELS, PHONON_MODEL_REPOS

logger = logging.getLogger(__name__)
server_log = logging.getLogger("PhononServer")

CREATE_NO_WINDOW = 0x08000000


@dataclass
class TranscribeResult:
    text: str = ""
    words: List[dict] = field(default_factory=list)   # [{"word": str, "start": s, "end": s}]
    error: bool = False                               # True if the request failed (distinct from "heard nothing")

    @property
    def timed(self) -> bool:
        return bool(self.words)


# ── Windows Job Object (child dies with parent) ──

class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_KILL_ON_CLOSE = 0x2000
_JOB_EXTENDED_LIMIT_INFO = 9


def _close_job(job):
    if job:
        k32 = ctypes.windll.kernel32
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle(job)


def _create_kill_on_close_job():
    """Returns a job handle (kept alive for the process lifetime) or None if unsupported."""
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_KILL_ON_CLOSE
        if not k32.SetInformationJobObject(job, _JOB_EXTENDED_LIMIT_INFO, ctypes.byref(info), ctypes.sizeof(info)):
            _close_job(job)
            return None
        return job
    except Exception as e:
        logger.debug(f"Job object unavailable: {e}")
        return None


class PhononEngine:
    name = PHONON_MODELS[DEFAULT_PHONON_MODEL]
    supports_live_stream = True

    def open_live_stream(self, on_partial):
        from src.core.live import PhononStream
        return PhononStream(self, on_partial)
    def __init__(self, port: int = 8010, host: str = "127.0.0.1", threads: Optional[int] = None,
                 model: str = DEFAULT_PHONON_MODEL, hotwords=None):
        if model not in PHONON_MODELS:
            raise ValueError(f"Unsupported Phonon model: {model}")
        self.model = model
        self.set_hotwords(hotwords)
        self.name = PHONON_MODELS[model]
        self.port = port
        self.host = host
        self.threads = threads
        self.base_url = f"http://{self.host}:{self.port}"
        self._process: Optional[subprocess.Popen] = None
        self._owns_process = False
        self._is_ready = False
        self._lock = threading.Lock()          # guards process handle only; never held while waiting
        self._start_lock = threading.Lock()    # serialises start/restart
        self._stopping = False
        self._stop_event = threading.Event()
        self._job = None
        self._recent_server_output: Deque[str] = collections.deque(maxlen=60)
        self._tls = threading.local()          # one keep-alive HTTP connection per calling thread
        self._server_conflict = ""
        self._health_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.on_state_change: Optional[Callable[[bool, str], None]] = None   # (ready, message)

    # ── Health ──

    def is_server_running(self) -> bool:
        self._server_conflict = ""
        try:
            req = urllib.request.Request(f"{self.base_url}/health", method="GET")
            with self._health_opener.open(req, timeout=1.5) as resp:
                if resp.status != 200:
                    return False
                data = json.loads(resp.read(65536))
                actual = str(data.get("repo") or data.get("model", "")).casefold()
                expected = {self.model, PHONON_MODEL_REPOS[self.model].casefold()}
                if data.get("kind") != "speech" or actual not in expected:
                    self._server_conflict = (
                        f"Port {self.port} serves {data.get('model', 'an unknown service')}; "
                        f"expected {self.name}. Close that server or choose another port.")
                    return False
                return True
        except Exception:
            return False

    def _set_ready(self, ready: bool, message: str = ""):
        changed = ready != self._is_ready
        self._is_ready = ready
        if changed and self.on_state_change:
            try:
                self.on_state_change(ready, message)
            except Exception:
                logger.debug("on_state_change callback failed", exc_info=True)

    # ── Process management ──

    def _kill_stale_servers(self):
        """Clean up only this instance's child; never kill other apps' servers."""
        if self._process is not None and self._owns_process:
            self._terminate_process()

    @staticmethod
    def _drain(stream, tag: str, sink: Deque[str]):
        """Continuously reads a child pipe so the child can never block on a full buffer."""
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode(errors="replace").rstrip()
                if not line:
                    continue
                sink.append(line)
                # Per-request access lines are noise; anything else may be a real diagnostic.
                if '"POST ' in line or '"GET ' in line:
                    server_log.debug(line)
                else:
                    server_log.info(f"[{tag}] {line}")
        except Exception:
            pass

    def _spawn(self):
        installed = importlib.metadata.version("fermion-research")
        if tuple(int(n) for n in installed.split(".")[:3]) < (0, 2, 14):
            raise RuntimeError("Phonon-2.5 needs fermion-research 0.2.14 or newer. "
                               "Run .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        venv_bin_dir = os.path.dirname(sys.executable)
        python = os.path.join(venv_bin_dir, "python.exe")
        if not os.path.exists(python):
            raise RuntimeError("The virtual environment's Python is missing. Reinstall requirements.txt.")
        # Avoid the fixed-model launcher and use console Python for captured
        # output under pythonw startup. Windows venv Python can redirect to a
        # child interpreter; the Job Object must cover and reap that child too.
        cmd = [python, "-c", "from fermion.cli import main; main()", "serve", self.model,
               "--host", self.host, "--port", str(self.port)]
        if self.threads:
            cmd += ["--threads", str(int(self.threads))]
        logger.info("Starting %s server: %s", self.name, ' '.join(cmd))
        env = os.environ.copy()
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        env["DO_NOT_TRACK"] = "1"

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
            env=env,
        )
        for stream, tag in ((proc.stdout, "out"), (proc.stderr, "err")):
            threading.Thread(target=self._drain, args=(stream, tag, self._recent_server_output),
                             daemon=True, name=f"ServerDrain-{tag}").start()

        if self._job is None:
            self._job = _create_kill_on_close_job()
        if self._job:
            try:
                k32 = ctypes.windll.kernel32
                k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
                k32.AssignProcessToJobObject(self._job, int(proc._handle))
            except Exception as e:
                logger.debug(f"Could not assign server to job object: {e}")
        return proc

    def start_server(self, wait_timeout: int = 120):
        """Starts (or adopts) the local server and blocks until /health is OK."""
        with self._start_lock:
            if self._stop_event.is_set():
                raise RuntimeError("Server start aborted (shutting down).")
            if self.is_server_running():
                logger.info("A healthy %s server is already running.", self.name)
                self._set_ready(True, "adopted")
                return
            if self._server_conflict:
                raise RuntimeError(self._server_conflict)

            self._kill_stale_servers()
            if self._stop_event.is_set():
                raise RuntimeError("Server start aborted (shutting down).")
            proc = self._spawn()
            with self._lock:
                self._process = proc
                self._owns_process = True

            try:
                start_time = time.monotonic()
                while time.monotonic() - start_time < wait_timeout:
                    if self._stopping:
                        raise RuntimeError("Server start aborted (shutting down).")
                    if proc.poll() is not None:
                        tail = " | ".join(list(self._recent_server_output)[-5:])
                        raise RuntimeError(f"{self.name} server exited with code {proc.returncode}. Output: {tail[:500]}")
                    if self.is_server_running():
                        logger.info("%s server is ready and healthy.", self.name)
                        self._set_ready(True, "ready")
                        return
                    if self._server_conflict:
                        raise RuntimeError(self._server_conflict)
                    self._stop_event.wait(1.0)
                raise TimeoutError(f"{self.name} server failed to start within {wait_timeout}s.")
            except Exception:
                self._terminate_process()
                raise

    def stop_server(self):
        """Terminates the server process if this instance started it."""
        self.request_stop()
        with self._start_lock:
            self._terminate_process()
            job, self._job = self._job, None
            _close_job(job)

    def request_stop(self):
        """Prevent startup/restarts immediately, while queued transcription finishes."""
        self._stopping = True
        self._stop_event.set()

    def _terminate_process(self):
        with self._lock:
            proc, owned = self._process, self._owns_process
            self._process = None
            self._owns_process = False
            job = self._job if owned else None
            if owned:
                self._job = None
        self._is_ready = False
        if proc and owned:
            logger.info("Stopping %s server process tree...", self.name)
            _close_job(job)  # includes Windows venv redirector children, on every restart
            try:
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    # ── Watchdog ──

    def start_watchdog(self, interval: float = 5.0, failures_before_restart: int = 3):
        """Background supervisor: restarts the server if it dies or stops answering /health."""
        def _loop():
            misses = 0
            while not self._stopping:
                if self._stop_event.wait(interval):
                    break
                if self._is_ready and self.is_server_running():
                    misses = 0
                    continue
                misses += 1
                proc = self._process
                dead = proc is not None and proc.poll() is not None
                if dead or not self._is_ready or misses >= failures_before_restart:
                    logger.error("Speech server is down or unresponsive; restarting it.")
                    self._set_ready(False, "restarting")
                    try:
                        self._terminate_process()
                        self.start_server(wait_timeout=120)
                    except Exception as e:
                        logger.error(f"Server restart failed: {e}")
                        self._set_ready(False, f"restart failed: {e}")
                    misses = 0

        threading.Thread(target=_loop, daemon=True, name="EngineWatchdog").start()

    # ── Transcription ──

    @staticmethod
    def _build_multipart(wav_data: bytes, fields: dict, boundary: str) -> bytes:
        crlf = b"\r\n"
        body = bytearray()
        for k, v in fields.items():
            body += f"--{boundary}".encode() + crlf
            body += f'Content-Disposition: form-data; name="{k}"'.encode() + crlf + crlf
            body += str(v).encode() + crlf
        body += f"--{boundary}".encode() + crlf
        body += b'Content-Disposition: form-data; name="file"; filename="dictation.wav"' + crlf
        body += b"Content-Type: audio/wav" + crlf + crlf
        body += wav_data + crlf
        body += f"--{boundary}--".encode() + crlf
        return bytes(body)

    @staticmethod
    def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        return buf.getvalue()

    def _connection(self, fresh: bool = False) -> http.client.HTTPConnection:
        """This thread's persistent keep-alive connection (each worker thread gets its own)."""
        conn = getattr(self._tls, "conn", None)
        if conn is not None and fresh:
            conn.close()
            conn = None
        if conn is None:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=30)
            self._tls.conn = conn
        return conn

    def transcribe(self, pcm_bytes: bytes, sample_rate: int = 16000) -> TranscribeResult:
        """Transcribes raw 16-bit mono PCM over a reused keep-alive connection.

        Each calling thread keeps one HTTP/1.1 connection open, so a decode no longer pays a TCP
        connect + teardown. A connection the server has dropped is replaced and the request
        retried once. Thread-safe: concurrent callers use separate connections (the server
        serialises the actual decodes).

        Returns TranscribeResult(error=True) on failure so callers can tell a failed request
        apart from a clip with no speech in it.
        """
        if not pcm_bytes:
            return TranscribeResult()

        boundary = "----PhononBoundary7f3a9c21"
        fields = {"model": self.model, "response_format": "verbose_json", "timestamp_granularities": "word"}
        terms = self.hotwords
        if terms:
            fields["hotwords"] = json.dumps(terms, ensure_ascii=False)
        body = self._build_multipart(
            self._pcm_to_wav(pcm_bytes, sample_rate),
            fields,
            boundary,
        )
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}

        last_err = ""
        for attempt in range(2):
            conn = self._connection(fresh=attempt > 0)
            try:
                conn.request("POST", "/v1/audio/transcriptions", body=body, headers=headers)
                resp = conn.getresponse()
                payload = resp.read()
                if resp.will_close:
                    self._connection(fresh=True)      # server is closing it; open a new one next time
                if resp.status != 200:
                    last_err = f"HTTP {resp.status}: {payload.decode(errors='replace')[:200]}"
                    break   # a 4xx/5xx answer is deterministic; retrying will not help
                data = json.loads(payload.decode())
                words = [
                    {"word": w.get("word", ""), "start": float(w.get("start", 0.0)), "end": float(w.get("end", 0.0))}
                    for w in data.get("words", []) or []
                ]
                return TranscribeResult(text=(data.get("text") or "").strip(), words=words)
            except Exception as e:
                # Typically a stale keep-alive socket (server restarted / idle-closed it).
                last_err = str(e) or type(e).__name__
                try:
                    conn.close()
                except Exception:
                    pass
                self._tls.conn = None
                if attempt == 0:
                    time.sleep(0.05)

        logger.error(f"Transcription failed: {last_err}")
        return TranscribeResult(error=True)

    def set_hotwords(self, terms):
        from src.config import recognition_terms
        self.hotwords = tuple(recognition_terms(terms))

    def transcribe_wav_bytes(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        """Plain-text convenience wrapper (kept for scripts/tests)."""
        return self.transcribe(pcm_bytes, sample_rate).text
