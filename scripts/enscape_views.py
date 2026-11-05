"""Advance an ordered capture job, yielding to Rhino between every step."""
import os
import math
import Rhino
import scriptcontext as sc
from enscape_bridge import KEY, PRESET_KEY
from enscape_capture import prepare_capture, preparation_status, request_capture, collect_capture
from enscape_renderer import status
from enscape_defaults import DEFAULTS_KEY
from enscape_presets import configure_preset, restore_preset


VIEWS_KEY = "bc_enscape_views"


def _finish_views(state):
    errors = []
    try:
        if not state["rhino_view"].ActiveViewport.SetViewProjection(state["camera"], True):
            raise RuntimeError("Could not restore the recorded camera")
        state["rhino_view"].Redraw()
        state["camera_restored"] = True
    except Exception as error:
        errors.append(str(error))
    for name in state["temporary_views"]:
        try:
            table = state["rhino_document"].NamedViews
            if table.FindByName(name) >= 0 and not table.Delete(name):
                raise RuntimeError("Could not delete temporary named view: " + name)
        except Exception as error:
            errors.append(str(error))
    if errors:
        state["restore_error"] = "; ".join(errors)
    state["phase"] = "complete"


def _view_settings(name, defaults):
    settings = dict(defaults["visual"])
    if name is not None:
        viewport = sc.doc.NamedViews[sc.doc.NamedViews.FindByName(name)].Viewport
        settings["ProjectionMode"] = ("ORTHOGRAPHIC" if viewport.IsParallelProjection else
                                      "TWO_POINT" if viewport.IsTwoPointPerspectiveProjection else
                                      "PERSPECTIVE")
        if not viewport.IsParallelProjection:
            # Rhino's Enscape camera parser sends the vertical field of view.
            valid, _, vertical, _ = viewport.GetCameraAngles()
            if not valid:
                raise ValueError("Cannot read named-view field of view: " + name)
            settings["FieldOfView"] = math.degrees(2 * vertical)
    settings.update({key: value for key, value in defaults["overrides"].items()
                     if key in ("ProjectionMode", "FieldOfView")})
    return settings


def _views_result(state):
    return {"complete": state["phase"] == "complete", "phase": state["phase"],
            "passed": state["phase"] == "complete" and state["camera_restored"] and
                      not state.get("restore_error") and all(item["state"] == "complete" for item in state["views"]),
            "failed_view": state.get("failed_view"), "camera_restored": state["camera_restored"],
            "restore_error": state.get("restore_error"), "views": [dict(item) for item in state["views"]]}


def _advance_views(state):
    item = state["views"][state["index"]]
    if state["phase"] == "settings_restore":
        if PRESET_KEY in sc.sticky:
            restore_preset()
        state["phase"] = "settings_apply"
    elif state["phase"] == "settings_apply":
        settings = _view_settings(item["named_view"], state["defaults"])
        configure_preset(settings)
        item["camera_settings"] = {key: settings[key] for key in ("ProjectionMode", "FieldOfView")}
        state["phase"] = "prepare"
    elif state["phase"] == "prepare":
        prepare_capture(item["capture_view"])
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
                state["phase"] = "settings_restore"


def capture_views(views):
    """Poll with ordered (named_view_or_None, absolute_new_png_path) pairs.

    Start the renderer and apply settings once before calling. Each invocation
    advances at most one step; never loop inside a Rook call. A terminal result
    is retained until a different list is supplied. The job restores the recorded
    active viewport camera and removes its temporary views on success or failure.
    None snapshots the active camera with sheet/request settings; saved views
    keep their projection and lens unless explicitly overridden. Settings and sun
    belong to the caller. Completed output files remain when a later view fails.
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
            if not os.path.isabs(path) or not path.lower().endswith(".png"):
                raise ValueError("Each view needs an absolute new PNG path")
            if name is not None and (not name or sc.doc.NamedViews.FindByName(name) < 0):
                raise ValueError("Named view does not exist: " + str(name))
            if os.path.exists(path):
                raise ValueError("Refusing existing output: " + path)
        if "capture_task" in sc.sticky.get(KEY, {}):
            raise ValueError("Collect the pending capture before starting a views job")
        ready = status()
        if not ready["running"] or ready["start_task"] != "RanToCompletion":
            raise ValueError("Start the intended renderer and wait for completion before capture_views")
        view = sc.doc.Views.ActiveView
        if DEFAULTS_KEY not in sc.sticky:
            raise ValueError("Run apply_defaults before capture_views")
        state = {"pairs": pairs, "document": sc.doc.RuntimeSerialNumber, "rhino_view": view,
                 "rhino_document": sc.doc,
                 "camera": Rhino.DocObjects.ViewportInfo(view.ActiveViewport), "camera_restored": False,
                 "phase": "settings_restore", "index": 0, "temporary_views": [],
                 "defaults": sc.sticky[DEFAULTS_KEY],
                 "views": [dict(named_view=name, capture_view=name, output=path, state="pending", files=[], pixels=None)
                           for name, path in pairs]}
        sc.sticky[VIEWS_KEY] = state
    if state["phase"] != "complete":
        try:
            if sc.doc.RuntimeSerialNumber != state["document"]:
                raise ValueError("The bound document changed during the views job")
            if any(item["capture_view"] is None for item in state["views"]):
                import System
                name = "bc-enscape-" + str(System.Guid.NewGuid())
                state["temporary_views"].append(name)
                if sc.doc.NamedViews.Add(name, state["rhino_view"].ActiveViewport.Id) < 0:
                    raise RuntimeError("Could not create temporary named view")
                for item in state["views"]:
                    if item["capture_view"] is None:
                        item["capture_view"] = name
                return _views_result(state)
            _advance_views(state)
        except Exception as error:
            item = state["views"][state["index"]]
            item.update(state="failed", error=str(error))
            state["failed_view"] = item["named_view"] if item["named_view"] is not None else "active viewport"
            _finish_views(state)
    return _views_result(state)
