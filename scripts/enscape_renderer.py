"""Start and close only this Rhino's job-owned hidden renderer."""
import System
import System.Windows.Forms as forms
import ctypes
from ctypes import wintypes
import scriptcontext as sc
from enscape_bridge import KEY, services


class _ProcessEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260)]


def _renderer_processes():
    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
    kernel.Process32NextW.argtypes = kernel.Process32FirstW.argtypes
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError()
    entry = _ProcessEntry()
    entry.size = ctypes.sizeof(entry)
    parent = System.Diagnostics.Process.GetCurrentProcess().Id
    matches = []
    try:
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.parent == parent and entry.name.lower() == "enscape.rendererhost.exe":
                matches.append(int(entry.pid))
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return [System.Diagnostics.Process.GetProcessById(pid) for pid in matches]


def _renderer_process():
    matches = _renderer_processes()
    if len(matches) != 1:
        parent = System.Diagnostics.Process.GetCurrentProcess().Id
        raise ValueError("Expected one renderer host belonging to Rhino {}: {}".format(parent, matches))
    return matches[0]


def _window_pid(handle):
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(handle), ctypes.byref(pid))
    return pid.value


def _record_windows(state):
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def record(handle, unused):
        if _window_pid(handle) == state["process"].Id:
            title = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(wintypes.HWND(handle), title, len(title))
            if title.value.startswith("Enscape"):
                state["handles"].add(int(handle))
                if title.value == "Enscape" or title.value.startswith(("Enscape -", "Enscape\u00a0-")):
                    state["renderer_window"] = int(handle)
        return True
    ctypes.windll.user32.EnumWindows(callback_type(record), 0)


def start_hidden():
    """Discover this Rhino's unique host; start once, then poll status()."""
    from enscape_job import remember_job
    previous = sc.sticky.get(KEY)
    if previous and previous.get("timer") is not None:
        raise ValueError("Monitor already active; poll status or close_renderer first")
    process = _renderer_process()
    service = services()
    if service["state"].CurrentState.IsRunning:
        raise ValueError("Renderer already running; this job will not take ownership")
    remember_job()
    state = {"services": service, "process": process, "handles": set(),
             "document": sc.doc.RuntimeSerialNumber, "started_by_job": True}
    timer = forms.Timer()
    timer.Interval = 100

    def hide_window(sender, args):
        if process.HasExited:
            sender.Stop()
            return
        _record_windows(state)
        for handle in state["handles"]:
            if _window_pid(handle) == process.Id and ctypes.windll.user32.IsWindowVisible(wintypes.HWND(handle)):
                ctypes.windll.user32.ShowWindowAsync(wintypes.HWND(handle), 0)

    timer.Tick += hide_window
    state["timer"] = timer
    state["hide_callback"] = hide_window
    sc.sticky[KEY] = state
    timer.Start()
    state["start_task"] = state["services"]["sync"].RequestStartRendererAsync()
    return status()


def status():
    state = sc.sticky[KEY]
    current = state["services"]["state"].CurrentState
    task = state["start_task"]
    return {
        "start_task": str(task.Status), "running": current.IsRunning,
        "renderer_process_id": state["process"].Id,
        "visible_window_count": sum(_window_pid(h) == state["process"].Id and
                                    bool(ctypes.windll.user32.IsWindowVisible(wintypes.HWND(h)))
                                    for h in state["handles"]),
        "error": str(task.Exception) if task.IsFaulted else None,
    }


def _stop_monitor():
    state = sc.sticky[KEY]
    if state.get("timer") is not None:
        state["timer"].Stop()
        state["timer"].Dispose()
        state["timer"] = None


def close_renderer():
    """Request close once; poll in later calls until complete. Never wait here."""
    state = sc.sticky.get(KEY)
    if not state or not state.get("started_by_job"):
        return {"complete": True, "closed": False, "reason": "No renderer owned by this job"}
    if sc.doc.RuntimeSerialNumber != state["document"]:
        raise ValueError("Bound document changed; refusing renderer close")
    if "capture_task" in state:
        raise ValueError("Collect the pending capture before closing the renderer")
    current = state["services"]["state"].CurrentState
    if current.IsRunning:
        from enscape_materials import _active_export
        _active_export()
    _record_windows(state)
    if not state.get("close_requested"):
        state["close_task"] = state["services"]["sync"].StopAllLiveSyncAsync()
        state["close_method"] = "StopAllLiveSyncAsync"
        state["close_requested"] = True
        return {"complete": False, "phase": "closing", "method": state["close_method"]}
    task = state.get("close_task")
    if task is not None and task.IsFaulted:
        raise RuntimeError(str(task.Exception))
    if task is not None and not task.IsCompleted:
        return {"complete": False, "phase": "stopping-sync"}
    handle = state.get("renderer_window")
    window_exists = handle is not None and not state["process"].HasExited and _window_pid(handle) == state["process"].Id
    if window_exists and not state.get("window_close_posted"):
        if not ctypes.windll.user32.PostMessageW(wintypes.HWND(handle), 0x0010, 0, 0):
            raise ctypes.WinError()
        state["window_close_posted"] = True
        state["close_method"] += "; recorded-window WM_CLOSE"
        return {"complete": False, "phase": "closing-window", "method": state["close_method"]}
    complete = not status()["running"] and not window_exists and state["start_task"].IsCompleted
    if complete:
        _stop_monitor()
    return {"complete": complete, "closed": complete, "method": state["close_method"],
            "monitor_stopped": state.get("timer") is None}
