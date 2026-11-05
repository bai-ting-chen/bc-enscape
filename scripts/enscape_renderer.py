"""Run and monitor the explicitly selected hidden renderer."""
import System
import System.Windows.Forms as forms
import ctypes
import scriptcontext as sc
from enscape_bridge import KEY, services


def start_hidden(renderer_process_id):
    """Hide this explicitly selected renderer host's window as it starts."""
    previous = sc.sticky.get(KEY)
    if previous and previous.get("timer") is not None:
        raise ValueError("Monitor already active; inspect status before starting again")
    process = System.Diagnostics.Process.GetProcessById(renderer_process_id)
    if process.ProcessName != "Enscape.RendererHost":
        raise ValueError("Selected process is not an Enscape renderer host")
    state = {"services": services(), "process": process, "handles": set()}
    timer = forms.Timer()
    timer.Interval = 100

    def hide_window(sender, args):
        if process.HasExited:
            sender.Stop()
            return
        process.Refresh()
        handle = process.MainWindowHandle.ToInt64()
        if handle:
            state["handles"].add(handle)
        for handle in state["handles"]:
            if ctypes.windll.user32.IsWindowVisible(handle):
                ctypes.windll.user32.ShowWindowAsync(handle, 0)

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
        "visible_window_count": sum(bool(ctypes.windll.user32.IsWindowVisible(h))
                                    for h in state["handles"]),
        "error": str(task.Exception) if task.IsFaulted else None,
    }


def stop_monitor():
    state = sc.sticky[KEY]
    if state.get("timer") is not None:
        state["timer"].Stop()
        state["timer"].Dispose()
        state["timer"] = None
