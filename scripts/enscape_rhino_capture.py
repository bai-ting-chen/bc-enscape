"""Capture a Rhino overlay base and geometry-only alpha mask, restoring state."""
import os
import Rhino
import System
import scriptcontext as sc
from enscape_job import camera_values


def capture_rhino_overlay(named_view, width_px, height_px, output_folder):
    """Write rhino-base.png and rhino-mask.png; None uses the current viewport."""
    if any(type(v) is not int or v <= 0 for v in (width_px, height_px)):
        raise ValueError("width_px and height_px must be positive integers")
    if not os.path.isabs(output_folder):
        raise ValueError("Output folder must be absolute")
    index = sc.doc.NamedViews.FindByName(named_view) if named_view is not None else -1
    if named_view is not None and index < 0:
        raise ValueError("Named view does not exist: " + str(named_view))
    paths = [os.path.join(output_folder, name) for name in ("rhino-base.png", "rhino-mask.png")]
    if any(os.path.exists(path) for path in paths):
        raise ValueError("Refusing existing Rhino screenshot outputs")
    view = sc.doc.Views.ActiveView
    viewport = view.ActiveViewport
    camera = Rhino.DocObjects.ViewportInfo(viewport)
    before = camera_values(viewport)
    display = viewport.DisplayMode
    display_modes_before = {str(mode.Id) for mode in Rhino.Display.DisplayModeDescription.GetDisplayModes()}
    created = []
    hidden = []
    failures = []
    undo = sc.doc.BeginUndoRecord("Capture Rhino overlay")
    try:
        if named_view is not None and not sc.doc.NamedViews.Restore(index, viewport):
            raise RuntimeError("Could not restore named camera")
        os.makedirs(output_folder, exist_ok=True)
        capture = Rhino.Display.ViewCapture()
        capture.Width, capture.Height = width_px, height_px
        capture.DrawAxes = capture.DrawGrid = capture.DrawGridAxes = False
        capture.ScaleScreenItems = False
        for path, mask in zip(paths, (False, True)):
            if mask:
                renderable = (Rhino.DocObjects.ObjectType.Brep, Rhino.DocObjects.ObjectType.Surface,
                              Rhino.DocObjects.ObjectType.Extrusion, Rhino.DocObjects.ObjectType.Mesh,
                              Rhino.DocObjects.ObjectType.SubD, Rhino.DocObjects.ObjectType.InstanceReference)
                for obj in sc.doc.Objects:
                    if obj.ObjectType not in renderable and obj.Attributes.Visible:
                        original = obj.Attributes.Duplicate()
                        hidden.append((obj.Id, original))
                        attributes = original.Duplicate()
                        attributes.Visible = False
                        if not sc.doc.Objects.ModifyAttributes(obj.Id, attributes, True):
                            raise RuntimeError("Could not hide non-renderable object: " + str(obj.Id))
                # Updating a copied mode persists it to settings.xml. Deletion only
                # removes the in-memory entry, which settings reload can resurrect.
                # Existing Shaded plus hidden non-geometry needs no registered mode.
                mode = Rhino.Display.DisplayModeDescription.GetDisplayMode(
                    Rhino.Display.DisplayModeDescription.ShadedId)
                if mode is None:
                    raise RuntimeError("Shaded display mode is unavailable")
                viewport.DisplayMode = mode
            capture.TransparentBackground = mask
            view.Redraw()
            bitmap = capture.CaptureToBitmap(view)
            if bitmap is None:
                raise RuntimeError("Rhino capture returned no bitmap")
            try:
                if bitmap.Width != width_px or bitmap.Height != height_px:
                    raise RuntimeError("Rhino capture pixel dimensions differ")
                bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png)
                created.append(path)
            finally:
                bitmap.Dispose()
    except Exception as error:
        failures.append("capture: " + str(error))
    finally:
        for identifier, attributes in hidden:
            if not sc.doc.Objects.ModifyAttributes(identifier, attributes, True):
                failures.append("object visibility restoration failed: " + str(identifier))
            elif sc.doc.Objects.FindId(identifier).Attributes.Visible != attributes.Visible:
                failures.append("object visibility restoration readback differs: " + str(identifier))
        viewport.DisplayMode = display
        restored = viewport.SetViewProjection(camera, True)
        viewport.SetCameraTarget(camera.TargetPoint, False)
        if not restored or camera_values(viewport) != before:
            failures.append("camera restoration readback differs")
        if {str(mode.Id) for mode in Rhino.Display.DisplayModeDescription.GetDisplayModes()} != display_modes_before:
            failures.append("display-mode inventory restoration readback differs")
        if viewport.DisplayMode.Id != display.Id:
            failures.append("display mode restoration readback differs")
        view.Redraw()
        if undo:
            sc.doc.EndUndoRecord(undo)
    if failures:
        for path in created:
            os.remove(path)
        raise RuntimeError("Rhino overlay capture failed: " + "; ".join(failures))
    return {"base_png": paths[0], "mask_png": paths[1], "pixels": [width_px, height_px],
            "camera_restored": True, "display_restored": True}
