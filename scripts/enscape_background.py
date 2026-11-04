"""Experimental Enscape 4.5 bridge hooks, called inside Rhino through Rook.

These are installed plug-in internals, not the public Chaos SDK. Start/capture
are asynchronous: return to Rhino's event loop, then call collect_capture().
"""
import ctypes
import json
import os
import shutil
import time

import Rhino
import System
import System.Windows.Forms as forms
import scriptcontext as sc

KEY = "bc_enscape_background"
PRESET_KEY = "bc_enscape_temporary_preset"
ACTIVE_PRESET_ID = "d8e4b713-533b-421b-8b11-a961385e4b37"
FLAGS = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance


def find_type(name):
    return next(assembly.GetType(name)
                for assembly in System.AppDomain.CurrentDomain.GetAssemblies()
                if assembly.GetType(name) is not None)


def service_provider():
    plugin_type = find_type("Enscape.Rhino.Plugin.EnscapeRhinoPlugin")
    plugin_id = plugin_type.GetProperty("PluginGuidIdentifier").GetValue(None)
    plugin = Rhino.PlugIns.PlugIn.Find(plugin_id)
    return plugin_type.GetField("_serviceProvider", FLAGS).GetValue(plugin)


def services():
    provider = service_provider()
    return {
        "sync": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RenderingSync.RhinoRenderingSync")),
        "state": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RenderingSync.CurrentRenderingStateProvider")),
        "screenshots": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RhinoStoredRenderings")),
    }


def typed_array(type_name, values):
    array = System.Array.CreateInstance(find_type(type_name), len(values))
    for index, value in enumerate(values):
        array.SetValue(value, index)
    return array


def embedded_presets():
    provider = service_provider()
    embedded = provider.GetService(find_type("Enscape.Rhino.Plugin.EmbeddedData.RhinoEmbeddedData"))
    sync = services()["sync"]
    document = sync.GetType().GetField("_documents", FLAGS).GetValue(sync).ActiveDocument
    return embedded.VisualSettingPresets, document


def set_preset_data(store, document, records):
    operations = []
    for identifier, payload in records:
        data = System.Activator.CreateInstance(find_type("Enscape.Cad.EmbeddedData.DataObject"),
                                              System.Array[System.Object]([identifier, payload]))
        operation = System.Activator.CreateInstance(find_type("Enscape.Cad.EmbeddedData.SetEmbeddedDataOperation"))
        operation.DataObject = data
        operations.append(operation)
    store.ExecuteOperations(document, typed_array("Enscape.Cad.EmbeddedData.CadEmbeddedDataOperation", operations))


def configure_lighting(width=3000, height=3000):
    """Select a temporary white-mode, transparent PNG preset without opening UI."""
    version = find_type("Enscape.Rhino.Plugin.EnscapeRhinoPlugin").Assembly.GetName().Version
    if (version.Major, version.Minor) != (4, 5):
        raise ValueError("Preset schema is verified only for Enscape 4.5")
    if PRESET_KEY in sc.sticky:
        raise ValueError("A temporary preset is already active; restore it first")
    if min(width, height) < 500:
        raise ValueError("Output dimensions must be at least 500 pixels")
    store, document = embedded_presets()
    before = [(item.Identifier, item.Data) for item in store.GetAllStoredData(document)]
    preset_id = System.Guid.NewGuid()
    identifier_type = find_type("Enscape.Cad.EmbeddedData.DataObjectIdentifier")

    def identifier(value, scope=""):
        return System.Activator.CreateInstance(identifier_type, System.Array[System.Object]([System.Guid(str(value)), scope]))

    # These values are Enscape 4.5 enums: WHITE=1, ORTHOGRAPHIC=2, CUSTOM=5.
    options = {"RenderingStyle": 1, "ProjectionMode": 2, "ScreenshotResolution": 5,
               "ScreenshotResolutionX": int(width), "ScreenshotResolutionY": int(height),
               "ScreenshotViewportAspectRatio": False, "ApplyAlphaChannelDuringImageExport": True,
               "ExportBackgroundMask": False, "WhiteBackground": True}
    payload = json.dumps({"Name": "background-lighting", "Settings": {"Version": 3,
                          "Settings": {key: {"Value": value} for key, value in options.items()}}})
    active = json.dumps({"Name": "ActivePresetId", "Settings": {"Version": 3,
                        "Settings": {"ActivePresetSetting": {"Value": str(preset_id)}}}})
    active_keys = [key for key, _ in before if str(key.Id) == ACTIVE_PRESET_ID]
    if not active_keys:
        active_keys = [identifier(ACTIVE_PRESET_ID)]
    records = [(identifier(preset_id), payload)] + [(key, active) for key in active_keys]
    sc.sticky[PRESET_KEY] = {"document": document, "store": store,
                             "before": before, "written_keys": [key for key, _ in records]}
    set_preset_data(store, document, records)
    return {"pixels": [width, height], "white_mode": True, "transparent": True}


def restore_preset():
    state = sc.sticky[PRESET_KEY]
    before = state["before"]
    operations = []
    for key in state["written_keys"]:
        if not any(key.Equals(previous) for previous, _ in before):
            operation = System.Activator.CreateInstance(find_type("Enscape.Cad.EmbeddedData.DeleteEmbeddedDataOperation"))
            operation.Identifier = key
            operations.append(operation)
    if operations:
        state["store"].ExecuteOperations(state["document"], typed_array("Enscape.Cad.EmbeddedData.CadEmbeddedDataOperation", operations))
    if before:
        set_preset_data(state["store"], state["document"], before)
    current = [(item.Identifier, item.Data) for item in state["store"].GetAllStoredData(state["document"])]
    if len(current) != len(before) or any(not any(key.Equals(old) and value == data for old, data in before) for key, value in current):
        raise ValueError("Preset restoration failed readback")
    del sc.sticky[PRESET_KEY]
    return {"preset_restored": True}


def prepare_capture(named_view):
    """Send the exact named camera, Rhino sun and named-view context to Enscape."""
    if sc.doc.NamedViews.FindByName(named_view) < 0:
        raise ValueError("Named view does not exist in the bound document")
    if any(obj.ObjectType == Rhino.DocObjects.ObjectType.ClipPlane for obj in sc.doc.Objects):
        raise ValueError("Clipping planes require an explicit capture context; this helper handles unclipped views")
    current = services()["state"].CurrentState
    provider = service_provider()
    constructor = find_type("Enscape.Rhino.Plugin.Views.RhinoView").GetConstructors()[0]
    args = []
    for parameter in constructor.GetParameters():
        if parameter.ParameterType.FullName == "System.String":
            args.append(named_view)
        elif parameter.ParameterType.FullName == "Enscape.Rhino.Plugin.Documents.RhinoDocument":
            args.append(current.RenderedDocument)
        else:
            service = provider.GetService(parameter.ParameterType)
            if service is None:
                raise ValueError("Missing Enscape service: " + parameter.ParameterType.FullName)
            args.append(service)
    state = sc.sticky[KEY]
    state["capture_view"] = constructor.Invoke(System.Array[System.Object](args))
    context = find_type("Enscape.Cad.RenderingSync.ViewportContext").GetMethod("CreateFromView").Invoke(
        None, System.Array[System.Object]([state["capture_view"]]))
    content = System.Activator.CreateInstance(find_type("Enscape.Cad.RenderingSync.ViewContent"))
    content.World = current.Session.CurrentWorld
    content.Camera = state["capture_view"].GetCamera()
    sun_parser = provider.GetService(find_type("Enscape.Rhino.Plugin.Views.RhinoSunParser"))
    content.SunPosition = sun_parser.ParseSun(sc.doc)
    planes = System.Activator.CreateInstance(find_type("Enscape.Cad.Views.GlobalSectionPlanes"))
    planes.SectionPlanes = System.Array.CreateInstance(find_type("GlmSharp.dvec4"), 0)
    content.GlobalSectionPlanes = planes
    state["context_task"] = current.LiveViewport.LiveViewport.InitializeContextAsync(context, content, True)
    return preparation_status()


def preparation_status():
    state = sc.sticky[KEY]
    task = state["context_task"]
    if task.IsFaulted or task.IsCanceled:
        raise RuntimeError(str(task.Exception))
    return {"preparation": str(task.Status)}


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
    context_task = state.get("context_task")
    if context_task is None or str(context_task.Status) != "RanToCompletion":
        raise ValueError("Run prepare_capture(named_view) and wait for preparation to finish first")
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
