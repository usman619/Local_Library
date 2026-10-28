"""
Qt-side client for ``app.core.doc_worker``.

One DocDaemon owns one child process that has its own copy of the open book. Requests are
fire-and-forget; results arrive as ``event`` signals on the GUI thread, already filtered so
a reply to a superseded/cancelled request is never delivered.
"""
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

# app/core/doc_daemon.py -> repository root (the directory that contains the `app` package)
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])


class DocDaemon(QObject):
    event = Signal(dict)

    def __init__(self, filepath: str, fontsize: float, parent: QObject | None = None):
        super().__init__(parent)
        self._filepath = filepath
        self._fontsize = float(fontsize)
        self._proc: QProcess | None = None
        self._buf = b""
        self._next_id = 0
        self._current_id: int | None = None
        self._stderr_tail = ""
        self._stopping = False

    # ------------------------------------------------------------------ public
    @property
    def current_id(self) -> int | None:
        return self._current_id

    def request(self, cmd: str, **params) -> int:
        """Starts the worker on first use. A new request supersedes any running one."""
        self._ensure_started()
        self._next_id += 1
        self._current_id = self._next_id
        self._send({"cmd": cmd, "id": self._current_id, **params})
        return self._current_id

    def cancel(self):
        """Abandons the current request; late replies for it are dropped."""
        if self._current_id is not None:
            self._send({"cmd": "cancel", "id": self._current_id})
        self._current_id = None

    def shutdown(self, wait_ms: int = 800):
        """Stops the child. Safe to call repeatedly / when it was never started."""
        self._current_id = None
        proc, self._proc = self._proc, None
        if proc is None:
            return
        self._stopping = True
        try:
            proc.readyReadStandardOutput.disconnect()
            proc.readyReadStandardError.disconnect()
            proc.finished.disconnect()
            proc.errorOccurred.disconnect()
        except (RuntimeError, TypeError):
            pass
        if proc.state() != QProcess.NotRunning:
            proc.closeWriteChannel()  # EOF on stdin makes the worker exit by itself
            if not proc.waitForFinished(wait_ms // 2):
                proc.kill()
                proc.waitForFinished(wait_ms // 2)
        proc.deleteLater()
        self._stopping = False

    # ---------------------------------------------------------------- internals
    def _ensure_started(self):
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            return
        self.shutdown(wait_ms=200)  # dispose of a dead process object, if any
        self._buf = b""
        self._stderr_tail = ""

        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        prev = env.value("PYTHONPATH", "")
        env.insert("PYTHONPATH", _PROJECT_ROOT + (os.pathsep + prev if prev else ""))
        env.insert("PYTHONUNBUFFERED", "1")
        proc.setProcessEnvironment(env)
        proc.setWorkingDirectory(_PROJECT_ROOT)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start(sys.executable, ["-u", "-m", "app.core.doc_worker"])
        self._send({"cmd": "open", "filepath": self._filepath, "fontsize": self._fontsize})

    def _send(self, payload: dict):
        if self._proc is not None:
            self._proc.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))

    def _on_stdout(self):
        if self._proc is None:
            return
        self._buf += bytes(self._proc.readAllStandardOutput())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            rid = ev.get("id")
            if rid is not None and rid != self._current_id:
                continue  # reply to a cancelled / superseded request
            self.event.emit(ev)

    def _on_stderr(self):
        if self._proc is None:
            return
        chunk = bytes(self._proc.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr_tail = (self._stderr_tail + chunk)[-600:]

    def _on_finished(self, exit_code: int, _status):
        if self._stopping:
            return
        self._proc = None
        had_request = self._current_id is not None
        self._current_id = None
        # Only worth reporting if someone was waiting for an answer.
        if had_request:
            detail = self._stderr_tail.strip().splitlines()[-1:] or [f"exit code {exit_code}"]
            self.event.emit({"event": "fatal", "error": "Background worker stopped: " + detail[0]})

    def _on_error(self, err):
        if self._stopping:
            return
        if err == QProcess.FailedToStart:
            self._proc = None
            self._current_id = None
            self.event.emit({"event": "fatal", "error": "Could not start the background worker."})