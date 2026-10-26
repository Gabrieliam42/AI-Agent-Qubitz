"""Selected-model file acquisition without inference, composed into every launcher."""
import os as _acq_os
import re as _acq_re
import threading as _acq_threading
from pathlib import Path as _AcqPath


def _qubitz_model_files_present(path):
    """Check the local file set, not full GGUF integrity or tensor payloads."""
    path = _AcqPath(path)
    split = _acq_re.fullmatch(r"(.+)-\d{5}-of-(\d{5})\.gguf", path.name)
    paths = (path,)
    if split:
        count = int(split[2])
        if not 1 <= count <= 256:
            return False
        paths = tuple(path.with_name(f"{split[1]}-{number:05}-of-{count:05}.gguf")
                      for number in range(1, count + 1))
    for candidate in paths:
        if not candidate.is_file() or candidate.stat().st_size < 24:
            return False
        with candidate.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                return False
    return True


def _qubitz_model_download_offline():
    enabled = {"1", "yes", "true", "on"}
    return (any(_acq_os.environ.get(name, "").strip().casefold() in enabled
                for name in ("QUBITZ_OFFLINE", "HF_HUB_OFFLINE"))
            or _acq_os.environ.get("QUBITZ_NETWORK_MODE", "").strip().casefold()
            in {"off", "offline", "disabled", "local", "local_only"})


def _install_selected_model_acquisition(app):
    base = app.base
    if getattr(base, "_QUBITZ_SELECTED_MODEL_ACQUISITION", False):
        return
    install_preflight = globals().get("_install_model_storage_preflight")
    if install_preflight is None:
        raise RuntimeError("Model storage preflight is unavailable; automatic acquisition was not installed.")
    install_preflight(base)
    original_resolve = base.resolve_gguf_model_path
    original_download = base.download_default_gguf_model
    locks_guard = _acq_threading.Lock()
    locks = {}

    def resolve_model(workspace, configured_path=None, preferred_filename=base.DEFAULT_GGUF_MODEL_FILENAME):
        explicit = configured_path or _acq_os.environ.get(base.GGUF_MODEL_PATH_ENV_NAME, "").strip()
        try:
            resolved = original_resolve(workspace, configured_path, preferred_filename)
        except FileNotFoundError:
            if explicit:
                raise
            return None
        if resolved is None:
            return None
        if _qubitz_model_files_present(resolved):
            return resolved
        if explicit:
            raise ValueError("Configured GGUF model files are missing, incomplete, or have an invalid header.")
        return None

    def download_model(workspace, filename=base.DEFAULT_GGUF_MODEL_FILENAME):
        key = (str(_AcqPath(workspace).resolve()), filename)
        with locks_guard:
            lock = locks.setdefault(key, _acq_threading.Lock())
        with lock:
            existing = resolve_model(workspace, None, filename)
            if existing is not None:
                return existing
            if _qubitz_model_download_offline():
                raise RuntimeError("Selected model files are missing or incomplete; downloads are disabled offline.")
            result = original_download(workspace, filename)
            if not _qubitz_model_files_present(result):
                raise RuntimeError("Model download did not produce the complete local GGUF file set; inference was not started.")
            return result

    download_model._qubitz_storage_preflight = True
    base.resolve_gguf_model_path = resolve_model
    base.download_default_gguf_model = download_model
    original_gui = base.QubitzGUI

    class ModelAcquisitionGUI(original_gui):
        def __init__(self, config):
            super().__init__(config)
            self._qubitz_acquisition_closed = _acq_threading.Event()
            self._qubitz_model_acquisition_thread = None
            self._qubitz_model_acquisition_state = {"state": "pending"}

            def close_acquisition(event):
                if event.widget is self.root:
                    self._qubitz_acquisition_closed.set()

            self.root.bind("<Destroy>", close_acquisition, add="+")
            self._schedule_selected_model_acquisition()

        def _schedule_selected_model_acquisition(self):
            self.root.after_idle(self._start_selected_model_acquisition)

        def _maybe_start_background_model_prewarm(self):
            prewarm = getattr(super(), "_maybe_start_background_model_prewarm", None)
            if prewarm is None or self._qubitz_acquisition_closed.is_set():
                return
            state = self._qubitz_model_acquisition_state["state"]
            if state in {"pending", "checking", "downloading"}:
                self.root.after(500, self._maybe_start_background_model_prewarm)
                return
            if state not in {"cached", "downloaded"}:
                return
            try:
                resolved = base.resolve_gguf_model_path(
                    base.config_runtime_workspace(self.config),
                    self.config.model_path,
                    self.config.selected_model_filename,
                )
            except (FileNotFoundError, OSError, ValueError):
                return
            if resolved is not None:
                prewarm()

        def _start_selected_model_acquisition(self):
            if self._qubitz_acquisition_closed.is_set() or self._qubitz_model_acquisition_thread is not None:
                return
            workspace = base.config_runtime_workspace(self.config)
            filename = self.config.selected_model_filename
            configured_path = self.config.model_path

            def report(state, message, **details):
                if self._qubitz_acquisition_closed.is_set():
                    return
                self._qubitz_model_acquisition_state = {"state": state, "filename": filename, **details}
                self.event_queue.put(("status", message))

            def acquire():
                try:
                    resolved = base.resolve_gguf_model_path(workspace, configured_path, filename)
                    if resolved is not None:
                        report("cached", f"Selected model files found: {filename}. Runtime validation is still required.")
                        return
                    if configured_path or _acq_os.environ.get(base.GGUF_MODEL_PATH_ENV_NAME, "").strip():
                        raise FileNotFoundError("The configured model path is missing; automatic substitution is disabled.")
                    if _qubitz_model_download_offline():
                        report("offline", f"Selected model files missing or incomplete: {filename}. Download disabled offline.")
                        return
                    if self._qubitz_acquisition_closed.is_set():
                        return
                    report("downloading", f"Selected model files missing or incomplete: {filename}. Preparing download (metadata and disk checks first).")
                    resolved = base.download_default_gguf_model(workspace, filename)
                    report("downloaded", f"Selected model file download finished: {filename}. Runtime validation is still required.",
                           path=str(resolved))
                except Exception as exc:  # noqa: BLE001 - Surface worker failures in the GUI instead of losing the thread.
                    report("error", f"Selected model file preparation failed: {type(exc).__name__}: {exc}", error=str(exc))

            report("checking", f"Checking selected model files: {filename}.")
            self._qubitz_model_acquisition_thread = _acq_threading.Thread(
                target=acquire, name="qubitz-selected-model-files", daemon=True,
            )
            self._qubitz_model_acquisition_thread.start()

    base.QubitzGUI = ModelAcquisitionGUI
    base._QUBITZ_SELECTED_MODEL_ACQUISITION = True


if "_APP" in globals():
    _install_selected_model_acquisition(globals()["_APP"])
