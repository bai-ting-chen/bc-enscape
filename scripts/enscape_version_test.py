"""Advance the reversible live Enscape compatibility test."""
import os
import scriptcontext as sc
from enscape_bridge import KEY, _preset_records_match, embedded_presets
from enscape_capture import collect_capture, preparation_status, prepare_capture, request_capture
from enscape_defaults import (
    _check_sheet_complete,
    _split_settings,
    apply_defaults,
    restore_settings,
)
from enscape_general import configure_general
from enscape_presets import configure_preset
from enscape_profile import profile_bytes
from enscape_renderer import status
from enscape_settings import settings_schema
from enscape_sheet import _read_defaults
from enscape_version import check_version


VERSION_TEST_KEY = "bc_enscape_version_test"


def _probe_value(entry):
    """Choose a different value using discovered enums or known bounds only."""
    current = entry["value"]
    if entry["enum"]:
        return next((name for name in entry["enum"] if name != current), None)
    if entry["type"] == "Boolean":
        return not current
    if entry["type"] in ("Single", "Double", "Int32", "UInt32"):
        return next((value for value in entry["range"].values()
                     if value != "unknown" and value != current), None)
    return None


def _version_category(state, category):
    entries = settings_schema()["categories"][category]
    sheet = _read_defaults()
    candidates = sorted(entries, key=lambda entry: (
        entry["key"] not in sheet, entry["type"] != "Boolean", not bool(entry["enum"])))
    for entry in candidates:
        value = _probe_value(entry) if entry["writable"] else None
        if value is not None:
            break
    else:
        raise ValueError("No writable test value discovered for " + category)
    key = entry["key"]
    try:
        configure = configure_general if category == "general" else configure_preset
        configure({key: value})
        actual = next(item["value"] for item in settings_schema()["categories"][category] if item["key"] == key)
        if actual != value:
            raise ValueError(key + " failed resolved readback")
    except Exception as error:
        state["items"][category] = {"passed": False, "key": key, "error": str(error)}
        raise
    finally:
        restore_settings()
    state["items"][category] = {"passed": True, "key": key, "value": value, "restored": True}


def _version_snapshot_matches(state):
    store, document = embedded_presets()
    current = [(item.Identifier, item.Data) for item in store.GetAllStoredData(document)]
    return profile_bytes() == state["profile"] and _preset_records_match(current, state["presets"])


def _version_capture(state):
    if state["phase"] == "prepare":
        apply_defaults({})
        prepare_capture(state["view"])
        state["phase"] = "preparing"
    elif state["phase"] == "preparing":
        if preparation_status()["preparation"] == "RanToCompletion":
            request_capture(os.path.join(state["folder"], "version-test.png"))
            state["phase"] = "capturing"
    elif state["phase"] == "capturing":
        capture = collect_capture()
        if capture["capture_task"] == "RanToCompletion":
            state["items"]["capture"] = dict(capture, passed=True)
            restore_settings()
            if not _version_snapshot_matches(state):
                raise ValueError("Version test changed the original profile or embedded presets")
            state["items"]["restoration"] = {"passed": True}
            state["phase"] = "complete"


def version_test(output_folder):
    """Advance one scratch-scene check; poll in later Rook calls until complete.

    Each call returns to Rhino's event loop. Reuse the same new folder throughout;
    a completed summary is retained until a different output folder is supplied.
    """
    if not os.path.isabs(output_folder):
        raise ValueError("Version test needs an absolute new output folder")
    folder = os.path.abspath(output_folder)
    state = sc.sticky.get(VERSION_TEST_KEY)
    if state and state["folder"] != folder and state["phase"] != "complete":
        raise ValueError("A version test is already in progress in " + state["folder"])
    if not state or state["folder"] != folder:
        if "capture_task" in sc.sticky.get(KEY, {}):
            raise ValueError("Collect the pending capture before testing settings")
        running = status()
        if not running["running"] or running["start_task"] != "RanToCompletion":
            raise ValueError("Start the intended renderer and wait for completion before the version test")
        capture_view = sc.sticky.get(KEY, {}).get("capture_view")
        if capture_view is not None:
            view = str(capture_view.Name)
        elif sc.doc.NamedViews.Count == 1:
            view = sc.doc.NamedViews[0].Name
        else:
            raise ValueError("Prepare the intended named view before testing a scene with multiple views")
        if os.path.exists(folder):
            raise ValueError("Version test output folder already exists: " + folder)
        restore_settings()
        state = {"folder": folder, "view": view, "version": check_version(), "items": {}, "phase": "sheet"}
        store, document = embedded_presets()
        state["profile"] = profile_bytes()
        state["presets"] = [(item.Identifier, item.Data) for item in store.GetAllStoredData(document)]
        sc.sticky[VERSION_TEST_KEY] = state
    try:
        if state["phase"] == "sheet":
            values = _read_defaults()
            _check_sheet_complete(values)
            _split_settings(values)
            state["items"]["sheet"] = {"passed": True, "keys": len(values)}
            state["remaining"] = list(settings_schema()["categories"])
            os.makedirs(folder)
            state["phase"] = "categories"
        elif state["phase"] == "categories":
            category = state["remaining"].pop(0)
            _version_category(state, category)
            if not state["remaining"]:
                state["phase"] = "prepare"
        elif state["phase"] != "complete":
            _version_capture(state)
    except Exception as error:
        state["items"][state["phase"]] = {"passed": False, "error": str(error)}
        # An in-flight screenshot must finish before its preset can be removed.
        task = sc.sticky.get(KEY, {}).get("capture_task")
        if task is None or task.IsCompleted:
            try:
                restore_settings()
                state["items"]["restoration"] = {"passed": _version_snapshot_matches(state)}
            except Exception as restore_error:
                state["items"]["restoration"] = {"passed": False, "error": str(restore_error)}
        else:
            state["items"]["restoration"] = {"passed": False, "error": "Capture pending; backups retained"}
        state["phase"] = "complete"
    return {"version": state["version"], "folder": folder, "phase": state["phase"],
            "complete": state["phase"] == "complete",
            "passed": state["phase"] == "complete" and all(item["passed"] for item in state["items"].values()),
            "items": state["items"]}
