"""Advance an ordered capture job, yielding to Rhino between every step."""
import os
import Rhino
import scriptcontext as sc
from enscape_bridge import KEY
from enscape_capture import prepare_capture, preparation_status, request_capture, collect_capture
from enscape_renderer import status


VIEWS_KEY = "bc_enscape_views"


def _finish_views(state):
    try:
        if not state["rhino_view"].ActiveViewport.SetViewProjection(state["camera"], True):
            raise RuntimeError("Could not restore the recorded camera")
        state["rhino_view"].Redraw()
        state["camera_restored"] = True
    except Exception as error:
        state["restore_error"] = str(error)
    state["phase"] = "complete"


def _views_result(state):
    return {"complete": state["phase"] == "complete", "phase": state["phase"],
            "passed": state["phase"] == "complete" and state["camera_restored"] and not state.get("failed_view"),
            "failed_view": state.get("failed_view"), "camera_restored": state["camera_restored"],
            "restore_error": state.get("restore_error"), "views": [dict(item) for item in state["views"]]}


def _advance_views(state):
    item = state["views"][state["index"]]
    if state["phase"] == "prepare":
        prepare_capture(item["named_view"])
        item["state"] = "preparing"
        state["phase"] = "preparing"
    elif state["phase"] == "preparing":
        preparation = preparation_status()
        if preparation["preparation"] == "RanToCompletion":
            item["section_plane_count"] = preparation.get("section_plane_count", 0)
            request_capture(item["output"])
            item["state"] = "capturing"
            state["phase"] = "capturing"
    elif state["phase"] == "capturing":
        capture = collect_capture()
        if capture["capture_task"] == "RanToCompletion":
            item.update(files=capture["files"], pixels=capture["pixels"], state="complete")
            state["index"] += 1
            if state["index"] == len(state["views"]):
                _finish_views(state)
            else:
                state["phase"] = "prepare"


def capture_views(views):
    """Poll with the same ordered (named_view, absolute_new_png_path) pairs.

    Start the renderer and apply settings once before calling. Each invocation
    advances at most one step; never loop inside a Rook call. A terminal result
    is retained until a different list is supplied. The job restores the recorded
    active viewport camera on success or failure; settings and sun belong to the
    caller. Completed output files remain when a later view fails.
    """
    pairs = tuple(tuple(pair) for pair in views)
    if not pairs or any(len(pair) != 2 for pair in pairs):
        raise ValueError("Supply an ordered list of (named view, absolute new PNG path)")
    state = sc.sticky.get(VIEWS_KEY)
    if state and state["pairs"] != pairs and state["phase"] != "complete":
        raise ValueError("A views job is already in progress; poll with the same arguments")
    if not state or state["pairs"] != pairs:
        paths = [os.path.normcase(os.path.abspath(path)) for _, path in pairs]
        if len(set(paths)) != len(paths):
            raise ValueError("Duplicate output paths in views job")
        for name, path in pairs:
            if not name or not os.path.isabs(path) or not path.lower().endswith(".png"):
                raise ValueError("Each named view needs an absolute new PNG path")
            if os.path.exists(path):
                raise ValueError("Refusing existing output: " + path)
        if "capture_task" in sc.sticky.get(KEY, {}):
            raise ValueError("Collect the pending capture before starting a views job")
        ready = status()
        if not ready["running"] or ready["start_task"] != "RanToCompletion":
            raise ValueError("Start the intended renderer and wait for completion before capture_views")
        view = sc.doc.Views.ActiveView
        state = {"pairs": pairs, "document": sc.doc.RuntimeSerialNumber, "rhino_view": view,
                 "camera": Rhino.DocObjects.ViewportInfo(view.ActiveViewport), "camera_restored": False,
                 "phase": "prepare", "index": 0,
                 "views": [dict(named_view=name, output=path, state="pending", files=[], pixels=None)
                           for name, path in pairs]}
        sc.sticky[VIEWS_KEY] = state
    if state["phase"] != "complete":
        try:
            if sc.doc.RuntimeSerialNumber != state["document"]:
                raise ValueError("The bound document changed during the views job")
            _advance_views(state)
        except Exception as error:
            item = state["views"][state["index"]]
            item.update(state="failed", error=str(error))
            state["failed_view"] = item["named_view"]
            _finish_views(state)
    return _views_result(state)
