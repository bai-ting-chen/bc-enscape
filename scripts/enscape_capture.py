"""Prepare, request and collect asynchronous image captures."""
import System
import os
import scriptcontext as sc
import shutil
import time
from enscape_bridge import FLAGS, KEY, find_type, service_provider, services
from enscape_renderer import status


def prepare_capture(named_view):
    """Send the exact named camera, Rhino sun and named-view context to Enscape."""
    if sc.doc.NamedViews.FindByName(named_view) < 0:
        raise ValueError("Named view does not exist in the bound document")
    if "capture_task" in sc.sticky[KEY]:
        raise ValueError("Collect the pending capture before preparing another view")
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
    state["capture_context"] = context
    state["capture_content"] = content
    state["capture_viewport"] = current.LiveViewport.LiveViewport
    state.pop("context_task", None)
    # Enscape restores this named view into a disposable viewport and uses its
    # own parser for the associated planes, including units and section depth.
    state["section_task"] = state["capture_view"].GetViewSectionPlanesAsync()
    return preparation_status()


def preparation_status():
    state = sc.sticky[KEY]
    if "section_task" in state:
        sections = state["section_task"]
        if sections.IsFaulted or sections.IsCanceled:
            raise RuntimeError(str(sections.Exception) if sections.IsFaulted else "Section discovery canceled")
        if not sections.IsCompleted:
            return {"preparation": str(sections.Status)}
        planes = System.Activator.CreateInstance(find_type("Enscape.Cad.Views.GlobalSectionPlanes"))
        planes.SectionPlanes = sections.Result.SectionPlanes
        state["section_plane_count"] = len(planes.SectionPlanes)
        state["capture_content"].GlobalSectionPlanes = planes
        state["context_task"] = state["capture_viewport"].InitializeContextAsync(
            state["capture_context"], state["capture_content"], True)
        del state["section_task"]
    task = state["context_task"]
    if task.IsFaulted or task.IsCanceled:
        raise RuntimeError(str(task.Exception))
    return {"preparation": str(task.Status), "section_plane_count": state.get("section_plane_count", 0)}


def screenshot_files():
    root = os.path.join(os.environ["TEMP"], "Enscape", "Screenshot")
    return {os.path.join(folder, name)
            for folder, _, names in os.walk(root)
            for name in names}


def request_capture(output_path):
    state = sc.sticky[KEY]
    context_task = state.get("context_task")
    if context_task is None or str(context_task.Status) != "RanToCompletion":
        raise ValueError("Run prepare_capture(named_view) and wait for preparation to finish first")
    if "capture_task" in state:
        raise ValueError("A capture is already pending; collect it first")
    if not output_path.lower().endswith(".png"):
        raise ValueError("The Rhino capture route only supports PNG")
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
    folders = {os.path.dirname(path) for path in candidates}
    if len(folders) != 1:
        raise ValueError("Cannot identify one capture directory; source files left untouched")
    # Each native capture owns one new GUID directory. Find its main image by
    # the common filename prefix, retaining every channel suffix verbatim.
    mains = [path for path in candidates if path.lower().endswith(".png") and all(
        other == path or os.path.basename(other).startswith(os.path.splitext(os.path.basename(path))[0] + "_")
        for other in candidates)]
    if len(mains) != 1:
        raise ValueError("Cannot identify the main image and channels; source files left untouched")
    source_main = mains[0]
    source_stem = os.path.splitext(os.path.basename(source_main))[0]
    target_stem = os.path.splitext(state["output_path"])[0]
    files = []
    for source in sorted(candidates):
        bitmap = System.Drawing.Bitmap(source)
        try:
            pixels = [bitmap.Width, bitmap.Height]
            if min(pixels) < 500:
                raise ValueError("Capture is unexpectedly small")
        finally:
            bitmap.Dispose()
        suffix = os.path.basename(source)[len(source_stem):]
        target = target_stem + suffix
        if os.path.exists(target):
            raise ValueError("Refusing to overwrite capture output: " + target)
        files.append({"source": source, "output": target, "pixels": pixels})
    if any(item["pixels"] != files[0]["pixels"] for item in files):
        raise ValueError("Capture channel sizes differ; source files left untouched")
    os.makedirs(os.path.dirname(state["output_path"]), exist_ok=True)
    created = []
    try:
        for item in files:
            with open(item["output"], "xb") as target:
                created.append(item["output"])
                with open(item["source"], "rb") as source:
                    shutil.copyfileobj(source, target)
    except Exception:
        for path in created:
            os.remove(path)
        raise
    for item in files:
        os.remove(item["source"])
    folder = folders.pop()
    if not os.listdir(folder):
        os.rmdir(folder)
    result = {
        "capture_task": str(task.Status), "pixels": files[0]["pixels"],
        "output": state["output_path"], "files": files,
        "seconds": time.time() - state["capture_started"],
        **status(),
    }
    del state["capture_task"]
    return result
