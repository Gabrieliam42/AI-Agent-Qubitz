"""GUI-owned queue wakeups, also composed into private standalone namespaces."""
import os as _gui_event_os
import threading as _gui_event_threading
from typing import Any as _GuiEventOwner


def _qubitz_install_gui_event_delivery(owner: _GuiEventOwner) -> None:
    if callable(getattr(owner, "_qubitz_close_gui_event_delivery", None)):
        return
    descriptors = None
    closed = False
    failed = False
    reader_registered = False
    resume_token = None
    lock = _gui_event_threading.Lock()
    queue = owner.event_queue
    original_put = queue.put
    owner._qubitz_gui_event_delivery = "timer"
    owner._qubitz_gui_event_delivery_error = ""

    def fallback(error):
        nonlocal failed
        failed = True
        owner._qubitz_gui_event_delivery = "timer"
        owner._qubitz_gui_event_delivery_error = f"{type(error).__name__}: {error}"

    def wake():
        with lock:
            if closed or failed or descriptors is None:
                return
            try:
                _gui_event_os.write(descriptors[1], b"x")
            except BlockingIOError:
                pass  # An unread wakeup already exists; queued messages are untouched.
            except OSError as error:
                fallback(error)

    def put(item, *args, **kwargs):
        result = original_put(item, *args, **kwargs)
        wake()
        return result

    def ready(descriptor, _mask):
        nonlocal reader_registered, resume_token
        if closed:
            return
        if failed:
            close()
            return
        try:
            if not _gui_event_os.read(descriptor, 8192):
                raise OSError("GUI wakeup pipe reached EOF")
        except BlockingIOError:
            pass
        except OSError as error:
            fallback(error)
            close()
            return
        if closed or getattr(owner, "_qubitz_gui_draining_events", False):
            return
        try:
            owner.root.deletefilehandler(descriptor)
            reader_registered = False
            try:
                owner._drain_gui_events()
            except Exception as error:  # noqa: BLE001 - Preserve Tk timer-callback error reporting.
                owner.root.report_callback_exception(type(error), error, error.__traceback__)
            if not closed and not failed:
                # A continuously readable pipe must not starve Tk's idle redraws.
                resume_token = owner.root.after_idle(resume)
        except owner.tk.TclError as error:
            fallback(error)
            close()

    def resume():
        nonlocal reader_registered, resume_token
        resume_token = None
        if closed or failed or descriptors is None:
            return
        try:
            owner.root.createfilehandler(descriptors[0], owner.tk.READABLE, ready)
            reader_registered = True
        except (OSError, owner.tk.TclError) as error:
            fallback(error)
            close()
            return
        if not queue.empty():
            wake()

    def close(event=None):
        nonlocal descriptors, closed, reader_registered, resume_token
        if event is not None and event.widget is not owner.root:
            return
        with lock:
            closed = True
            pair, descriptors = descriptors, None
            if queue.put is put:
                queue.put = original_put
            if resume_token is not None:
                try:
                    owner.root.after_cancel(resume_token)
                except owner.tk.TclError:
                    pass
                resume_token = None
            if pair is not None:
                if reader_registered:
                    try:
                        owner.root.deletefilehandler(pair[0])
                    except owner.tk.TclError:
                        pass
                    reader_registered = False
                for descriptor in pair:
                    try:
                        _gui_event_os.close(descriptor)
                    except OSError:
                        pass

    if callable(getattr(owner.root, "createfilehandler", None)):
        pair = None
        try:
            pair = _gui_event_os.pipe()
            for descriptor in pair:
                _gui_event_os.set_blocking(descriptor, False)
            owner.root.createfilehandler(pair[0], owner.tk.READABLE, ready)
            reader_registered = True
            descriptors = pair
        except (OSError, owner.tk.TclError) as error:
            fallback(error)
            if pair is not None:
                for descriptor in pair:
                    try:
                        _gui_event_os.close(descriptor)
                    except OSError:
                        pass
    owner._qubitz_wake_gui_events = wake
    owner._qubitz_close_gui_event_delivery = close
    owner.root.bind("<Destroy>", close, add="+")
    if descriptors is not None:
        queue.put = put
        owner._qubitz_gui_event_delivery = "fileevent"
        if not queue.empty():
            wake()
