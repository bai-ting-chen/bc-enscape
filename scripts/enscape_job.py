"""Retain and restore the camera and sun for one render job."""
import math
import Rhino
import System
import scriptcontext as sc


JOB_KEY = "bc_enscape_job"
_SUN_FIELDS = ("Enabled", "ManualControl", "Accuracy", "SkylightOn", "DaylightSaving",
               "DaylightSavingMinutes", "TimeZone", "North", "Intensity", "Latitude",
               "Longitude", "Azimuth", "Altitude")


def camera_values(viewport):
    info = Rhino.DocObjects.ViewportInfo(viewport)
    return (str(info.CameraLocation), str(info.CameraDirection), str(info.CameraUp),
            str(info.TargetPoint), info.IsParallelProjection, info.IsTwoPointPerspectiveProjection,
            info.Camera35mmLensLength, tuple(info.GetFrustum()))


def remember_job():
    from enscape_bridge import embedded_presets
    from enscape_profile import profile_bytes
    from enscape_settings import resolved_settings
    state = sc.sticky.get(JOB_KEY)
    if state and not state.get("complete"):
        if state["document"] != sc.doc.RuntimeSerialNumber:
            raise ValueError("Restore the previous job before changing documents")
        return state
    view = sc.doc.Views.ActiveView
    sun = sc.doc.Lights.Sun
    store, document = embedded_presets()
    state = {"document": sc.doc.RuntimeSerialNumber, "view": view,
             "camera": Rhino.DocObjects.ViewportInfo(view.ActiveViewport),
             "camera_values": camera_values(view.ActiveViewport),
             "sun": {key: getattr(sun, key) for key in _SUN_FIELDS},
             "sun_date": sun.GetDateTime(System.DateTimeKind.Utc), "sun_hash": sun.Hash,
             "profile": profile_bytes(),
             "presets": [(item.Identifier, item.Data) for item in store.GetAllStoredData(document)],
             "settings": resolved_settings()}
    sc.sticky[JOB_KEY] = state
    return state


def set_sun(azimuth_deg, altitude_deg):
    """Set degrees, retaining the original sun once until restore_job completes."""
    if (not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (azimuth_deg, altitude_deg))
            or not 0 <= azimuth_deg <= 360 or not -90 <= altitude_deg <= 90):
        raise ValueError("Sun requires azimuth_deg in 0..360 and altitude_deg in -90..90")
    remember_job()
    sun = sc.doc.Lights.Sun
    sun.SetPosition(azimuth_deg, altitude_deg)
    if abs(sun.Azimuth - azimuth_deg) > 1e-7 or abs(sun.Altitude - altitude_deg) > 1e-7:
        raise RuntimeError("Sun position failed degree readback")
    return {"azimuth_deg": sun.Azimuth, "altitude_deg": sun.Altitude}


def restore_job():
    """Poll to restore camera, sun, settings/profile and this job's renderer."""
    from enscape_defaults import restore_settings
    from enscape_renderer import close_renderer
    state = sc.sticky.get(JOB_KEY)
    if state and state["document"] != sc.doc.RuntimeSerialNumber:
        raise ValueError("Bound document changed; refusing job restore")
    failures = []
    result = {}
    if state and not state.get("restored"):
        try:
            view = state["view"]
            if not view.ActiveViewport.SetViewProjection(state["camera"], True):
                raise RuntimeError("SetViewProjection refused camera")
            view.ActiveViewport.SetCameraTarget(state["camera"].TargetPoint, False)
            view.Redraw()
            if camera_values(view.ActiveViewport) != state["camera_values"]:
                raise RuntimeError("camera readback differs")
            result["camera_restored"] = True
        except Exception as error:
            failures.append("camera: " + str(error))
        try:
            sun = sc.doc.Lights.Sun
            sun.SetDateTime(state["sun_date"], System.DateTimeKind.Utc)
            for key, value in state["sun"].items():
                setattr(sun, key, value)
            differing = [key for key, value in state["sun"].items() if getattr(sun, key) != value]
            if sun.Hash != state["sun_hash"]:
                differing.append("Hash")
            if differing:
                raise RuntimeError("readback differs: " + ", ".join(differing))
            result["sun_restored"] = True
        except Exception as error:
            failures.append("sun: " + str(error))
    try:
        result["settings"] = restore_settings()
    except Exception as error:
        failures.append("settings/profile: " + str(error))
    try:
        result["renderer"] = close_renderer()
    except Exception as error:
        failures.append("renderer: " + str(error))
    if failures:
        raise RuntimeError("Job restoration failed: " + "; ".join(failures))
    result["complete"] = result["renderer"]["complete"]
    if state and result["complete"]:
        from enscape_bridge import embedded_presets, _preset_records_match
        from enscape_profile import profile_bytes
        from enscape_settings import resolved_settings
        checks = {
            "camera": lambda: camera_values(state["view"].ActiveViewport) == state["camera_values"],
            "sun": lambda: sc.doc.Lights.Sun.Hash == state["sun_hash"],
            "profile": lambda: profile_bytes() == state["profile"],
            "settings": lambda: resolved_settings() == state["settings"],
        }
        def presets_match():
            store, document = embedded_presets()
            return _preset_records_match([(i.Identifier, i.Data) for i in store.GetAllStoredData(document)], state["presets"])
        checks["presets"] = presets_match
        for name, check in checks.items():
            try:
                if not check():
                    failures.append(name + " readback differs")
            except Exception as error:
                failures.append(name + ": " + str(error))
        if failures:
            raise RuntimeError("Job restoration failed: " + "; ".join(failures))
        result["readback"] = {name: True for name in checks}
    if state:
        state["restored"] = True
        state["complete"] = result["complete"]
    return result
