"""Experimental Enscape 4.5 bridge hooks, called inside Rhino through Rook.

These are installed plug-in internals, not the public Chaos SDK. Start/capture
are asynchronous: return to Rhino's event loop, then call collect_capture().
"""
import ctypes
import os
import shutil
import time

import Rhino
import System
import System.Windows.Forms as forms
import scriptcontext as sc

KEY = "bc_enscape_background"
FLAGS = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance


def find_type(name):
    return next(assembly.GetType(name)
                for assembly in System.AppDomain.CurrentDomain.GetAssemblies()
                if assembly.GetType(name) is not None)


def services():
    plugin_type = find_type("Enscape.Rhino.Plugin.EnscapeRhinoPlugin")
    plugin_id = plugin_type.GetProperty("PluginGuidIdentifier").GetValue(None)
    plugin = Rhino.PlugIns.PlugIn.Find(plugin_id)
    provider = plugin_type.GetField("_serviceProvider", FLAGS).GetValue(plugin)
    return {
        "sync": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RenderingSync.RhinoRenderingSync")),
        "state": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RenderingSync.CurrentRenderingStateProvider")),
        "screenshots": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RhinoStoredRenderings")),
    }


def screenshot_files():
    root = os.path.join(os.environ["TEMP"], "Enscape", "Screenshot")
    return {os.path.join(folder, name)
            for folder, _, names in os.walk(root)
            for name in names if name.lower().endswith(".png")}


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


def request_capture(output_path):
    state = sc.sticky[KEY]
    if "capture_task" in state:
        raise ValueError("A capture is already pending; collect it first")
    if not os.path.isabs(output_path):
        raise ValueError("Output path must be absolute")
    ready = status()
    if ready["start_task"] != "RanToCompletion" or not ready["running"]:
        raise ValueError("Renderer startup has not completed")
    if os.path.exists(output_path):
        raise ValueError("Refusing to overwrite an existing output")
    current = state["services"]["state"].CurrentState
    args = System.Activator.CreateInstance(find_type(
        "Enscape.Cad.RenderingSync.TakeScreenshotRequestEventArgs"))
    args.LiveViewport = current.LiveViewport.LiveViewport
    screenshots = state["services"]["screenshots"]
    handler = screenshots.GetType().GetField("TakeScreenshotRequested", FLAGS).GetValue(screenshots)
    state["files_before"] = screenshot_files()
    state["output_path"] = output_path
    state["capture_started"] = time.time()
    handler.DynamicInvoke(System.Array[System.Object]([screenshots, args]))
    state["capture_task"] = args.WaitAsync()
    return {"capture_task": str(state["capture_task"].Status)}


def collect_capture():
    state = sc.sticky[KEY]
    task = state["capture_task"]
    if not task.IsCompleted:
        return {"capture_task": str(task.Status)}
    if task.IsFaulted:
        raise RuntimeError(str(task.Exception))
    if task.IsCanceled:
        raise RuntimeError("Capture was canceled")
    candidates = screenshot_files() - state["files_before"]
    if len(candidates) != 1:
        raise ValueError("Cannot identify exactly one capture; source files left untouched")
    source = candidates.pop()
    bitmap = System.Drawing.Bitmap(source)
    try:
        pixels = [bitmap.Width, bitmap.Height]
        if min(pixels) < 500:
            raise ValueError("Capture is unexpectedly small")
    finally:
        bitmap.Dispose()
    os.makedirs(os.path.dirname(state["output_path"]), exist_ok=True)
    if os.path.exists(state["output_path"]):
        raise ValueError("Output appeared during capture; refusing to overwrite")
    shutil.move(source, state["output_path"])
    if not os.listdir(os.path.dirname(source)):
        os.rmdir(os.path.dirname(source))
    result = {
        "capture_task": str(task.Status), "pixels": pixels,
        "output": state["output_path"],
        "seconds": time.time() - state["capture_started"],
        **status(),
    }
    del state["capture_task"]
    return result


def stop_monitor():
    state = sc.sticky[KEY]
    if state.get("timer") is not None:
        state["timer"].Stop()
        state["timer"].Dispose()
        state["timer"] = None
