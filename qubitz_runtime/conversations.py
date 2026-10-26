"""Ordered local transcripts, independent of GUI display and retrieval storage."""
import base64 as _conversation_base64
import hashlib as _conversation_hashlib
import os as _conversation_os
import queue as _conversation_queue
import shutil as _conversation_shutil
import subprocess as _conversation_subprocess
import sys as _conversation_sys
import threading as _conversation_threading
import uuid as _conversation_uuid
from datetime import datetime as _ConversationDatetime
from datetime import timezone as _ConversationTimezone
from pathlib import Path as _ConversationPath

RESERVE_BYTES = 20 * 1024**3


class ConversationJournal:
    def __init__(self, root: _ConversationPath, *, pending_bytes: int = 16 * 1024**2,
                 reserve_bytes: int = RESERVE_BYTES) -> None:
        self.root = _ConversationPath(root).resolve()
        self.path = self.root / "conversations" / (_ConversationDatetime.now(_ConversationTimezone.utc).strftime("%Y%m%dT%H%M%S")
                                                   + "_" + _conversation_uuid.uuid4().hex + ".txt")
        self.error = ""
        self._reserve = reserve_bytes
        self._limit = pending_bytes
        self._pending = 0
        self._serial = 0
        self._closed = False
        self._lock = _conversation_threading.Lock()
        self._queue: _conversation_queue.Queue = _conversation_queue.Queue(maxsize=8192)
        self._thread = _conversation_threading.Thread(target=self._write, name="qubitz-conversation", daemon=False)
        self._thread.start()

    def _fail(self, message: str) -> None:
        if not self.error:
            self.error = "Conversation retention incomplete: " + message
            print(self.error, file=_conversation_sys.stderr)

    def record(self, role: str, text: str) -> bool:
        if not isinstance(text, str):
            text = str(text)
        size = 4 * (len(text) + len(role) + 100)
        with self._lock:
            if self.error or self._closed:
                return False
            if self._pending + size > self._limit or self._queue.full():
                self._fail("bounded recording queue exhausted; no full-retention claim is valid.")
                return False
            self._serial += 1
            self._pending += size
            stamp = _ConversationDatetime.now(_ConversationTimezone.utc).isoformat()
            self._queue.put_nowait((self._serial, stamp, role, text, size))
        return True

    def _write(self) -> None:
        stream = None
        stream_role = None
        try:
            self.path.parent.mkdir(exist_ok=True)
            if self.path.parent.is_symlink() or self.path.parent.resolve().parent != self.root:
                raise OSError("conversation directory must not redirect outside the application root")
            if _conversation_shutil.disk_usage(self.root).free <= self._reserve + 4096:
                raise OSError("20 GiB reserve prevents transcript creation")
            stream = self.path.open("xb")
            stream.write(b"\xef\xbb\xbfQubitz emitted conversation events; ordered by capture sequence.\n"
                         b"Encoding: UTF-8 with BOM. Adjacent stream fragments are concatenated unchanged.\n"
                         b"Stream and final/display records are labeled separately; no hidden reasoning is invented.\n")
            while True:
                try:
                    item = self._queue.get(timeout=0.2)
                except _conversation_queue.Empty:
                    if self._closed:
                        break
                    continue
                if item is None:
                    break
                if isinstance(item, _conversation_threading.Event):
                    stream.flush()
                    _conversation_os.fsync(stream.fileno())
                    item.conversation_offset = stream.tell()
                    item.set()
                    continue
                serial, stamp, role, text, size = item
                next_stream_role = role if role in {"reasoning", "assistant_delta"} else None
                if next_stream_role is not None and next_stream_role == stream_role:
                    packet = text.encode("utf-8")
                else:
                    separator = "\n\n" if stream_role is not None else "\n"
                    suffix = "" if next_stream_role is not None else "\n"
                    packet = f"{separator}[{serial:08d} {stamp} {role}]\n{text}{suffix}".encode()
                with self._lock:
                    self._pending -= size
                if _conversation_shutil.disk_usage(self.root).free - len(packet) <= self._reserve:
                    raise OSError("20 GiB reserve prevents further transcript recording")
                stream.write(packet)
                stream_role = next_stream_role
                stream.flush()
            if self.error:
                raise OSError(self.error)
            stream.flush()
            _conversation_os.fsync(stream.fileno())
        except (OSError, ValueError) as exc:
            self._fail(f"{type(exc).__name__}: {exc}")
            if stream is not None and _conversation_shutil.disk_usage(self.root).free > self._reserve + 4096:
                stream.write(("\n[RECORDING ERROR]\n" + self.error + "\n").encode("utf-8"))
        finally:
            if stream is not None:
                stream.close()

    def snapshot(self, timeout: float = 15.0) -> str:
        if self.error:
            raise RuntimeError(self.error)
        barrier = _conversation_threading.Event()
        with self._lock:
            if self._closed:
                raise RuntimeError("Conversation journal is closed")
            self._queue.put_nowait(barrier)
        if not barrier.wait(timeout) or self.error:
            raise RuntimeError(self.error or "Conversation flush timed out; full retention is unverified")
        # Read only through the barrier's complete data; later events remain ordered in the file.
        with self.path.open("rb") as stream:
            return stream.read(barrier.conversation_offset).decode("utf-8-sig")

    def close(self, timeout: float = 0.0) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._queue.put_nowait(None)
                except _conversation_queue.Full:
                    self._fail("recording queue could not close cleanly")
        if timeout:
            self._thread.join(timeout)
            if self._thread.is_alive():
                self._fail("close timed out; full retention is unverified")


class RecordingQueue:
    """Intercept producer order before GUI coalescing, trimming or hiding."""
    def __init__(self, wrapped, journal: ConversationJournal) -> None:
        self.wrapped = wrapped
        self.journal = journal

    def put(self, item, *args, **kwargs):
        if isinstance(item, tuple) and len(item) == 2:
            kind, text = item
            # Reasoning is captured at emission, including when display is disabled.
            if kind != "reasoning_delta":
                self.journal.record(str(kind), text)
        return self.wrapped.put(item, *args, **kwargs)

    def put_nowait(self, item):
        return self.put(item, block=False)

    def __getattr__(self, name):
        return getattr(self.wrapped, name)


def copy_windows_text(text: str) -> dict:
    """Windows owns the clipboard after this process exits, unlike transient X selections."""
    executable = _conversation_shutil.which("powershell.exe")
    if not executable:
        raise RuntimeError("Windows PowerShell clipboard bridge is unavailable")
    expected = _conversation_hashlib.sha256(text.encode("utf-8")).hexdigest()
    # stdin carries UTF-8 base64, never command syntax or shell-interpolated text.
    script = """$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
$value=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd()))
[Windows.Forms.Clipboard]::SetText($value)
$actual=[Windows.Forms.Clipboard]::GetText()
$sha=[Security.Cryptography.SHA256]::Create()
$hash=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($actual))).Replace('-','').ToLowerInvariant()
Write-Output $hash
"""
    encoded = _conversation_base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    command = [executable, "-NoProfile", "-NonInteractive", "-STA", "-EncodedCommand", encoded]
    if _conversation_sys.platform.startswith("linux") and _ConversationPath("/init").is_file():
        command = ["/init", *command]
    result = _conversation_subprocess.run(command, input=_conversation_base64.b64encode(text.encode("utf-8")).decode("ascii"),
                            text=True, capture_output=True, timeout=30, check=True)
    if result.stdout.strip() != expected:
        raise RuntimeError("Windows clipboard readback did not match the complete transcript")
    return {"sha256": expected, "utf8_bytes": len(text.encode("utf-8")), "windows_readback_equal": True}


def install_gui_archive(original):
    class ConversationGUI(original):
        def __init__(self, config):
            self.conversation_journal = ConversationJournal(_ConversationPath(config.runtime_workspace or config.workspace))
            self._copy_results = _conversation_queue.Queue()
            self._archive_error_reported = False
            self._copy_busy = False
            try:
                super().__init__(config)
                self.event_queue = RecordingQueue(self.event_queue, self.conversation_journal)
                self.clipboard_button = self.ttk.Button(self.send_button.master, text="Clipboard",
                                                       command=self._copy_all_conversation)
                self.clipboard_button.pack(fill="x", pady=(4, 0), before=self.send_button)
                self.root.bind("<Destroy>", self._archive_destroyed, add="+")
                self.root.after(200, self._check_archive_status)
            except BaseException:
                self.conversation_journal.close(timeout=2)
                raise

        def _archive_destroyed(self, event):
            if event.widget == self.root:
                self.conversation_journal.close()

        def _check_archive_status(self):
            error = self.conversation_journal.error
            if error and not self._archive_error_reported:
                self._archive_error_reported = True
                self._append_transcript("error", error)
                self.status_var.set(error)
            try:
                success, message = self._copy_results.get_nowait()
            except _conversation_queue.Empty:
                pass
            else:
                self._copy_busy = False
                self.clipboard_button.configure(state="normal")
                self._append_transcript("status" if success else "error", message)
            self.root.after(200, self._check_archive_status)

        def _append_transcript(self, role, message):
            self.conversation_journal.record("display:" + str(role), message)
            return super()._append_transcript(role, message)

        def _start_prompt(self, prompt, *, queued=False):
            broker = getattr(self.agent, "_tool_permission_broker", None)
            if broker is not None:
                broker.conversation_journal = self.conversation_journal
            return super()._start_prompt(prompt, queued=queued)

        def _copy_all_conversation(self):
            if self._copy_busy:
                return
            self._copy_busy = True
            self.clipboard_button.configure(state="disabled")

            def worker():
                try:
                    text = self.conversation_journal.snapshot()
                    receipt = copy_windows_text(text)
                    self.last_clipboard_receipt = receipt
                    self._copy_results.put((True, "Complete conversation copied to Windows clipboard."))
                except (OSError, RuntimeError, ValueError, _conversation_subprocess.SubprocessError, _conversation_queue.Full) as exc:
                    self._copy_results.put((False, f"Clipboard copy failed: {type(exc).__name__}: {exc}"))

            _conversation_threading.Thread(target=worker, name="qubitz-copy-all", daemon=True).start()

    return ConversationGUI
