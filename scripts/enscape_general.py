"""Apply and restore temporary user-wide general settings."""
import json
import os
import scriptcontext as sc
from enscape_bridge import PRESET_KEY
from enscape_profile import (
    _profile_backup_path,
    _read_profile_backup,
    _write_profile_backup,
    profile_bytes,
    profile_path,
    write_profile,
)
from enscape_settings import validate_settings


GENERAL_KEY = "bc_enscape_temporary_general"


def configure_general(settings):
    """Temporarily edit profile settings, preserving the file's original bytes.

    Enscape loads these values at host initialization. This edits the profile,
    not live hosts; restarting another document's host is never implicit.
    """
    if GENERAL_KEY in sc.sticky or os.path.exists(_profile_backup_path()):
        raise ValueError("Temporary general settings already exist; restore them first")
    if PRESET_KEY in sc.sticky:
        raise ValueError("Restore the temporary visual preset before editing the profile")
    changes = validate_settings(settings, general=True)
    before = profile_bytes()
    profile = json.loads(before.decode("utf-8-sig")) if before is not None else {"Version": 3, "Settings": {}}
    for key, value in changes.items():
        profile["Settings"][key] = {"Value": value}
    after = json.dumps(profile, indent=2).encode("utf-8")
    _write_profile_backup(before, after)
    sc.sticky[GENERAL_KEY] = {"before": before, "after": after}
    try:
        write_profile(after)
        if profile_bytes() != after:
            raise ValueError("General settings write failed readback")
    except Exception:
        restore_general()
        raise
    return {"changed": settings, "stored_readback": True,
            "application": "Restart the intended renderer host to load these profile values"}


def restore_general():
    backup = _profile_backup_path()
    state = _read_profile_backup() if os.path.exists(backup) else sc.sticky[GENERAL_KEY]
    if GENERAL_KEY in sc.sticky and sc.sticky[GENERAL_KEY] != state:
        raise ValueError("Profile backups disagree; backups retained")
    current = profile_bytes()
    if current not in (state["before"], state["after"]):
        raise ValueError("Profile changed externally; backup retained, refusing to replace it")
    if state["before"] is None:
        if current is not None:
            os.remove(profile_path())
    else:
        write_profile(state["before"])
    if profile_bytes() != state["before"]:
        raise ValueError("General restoration failed byte readback")
    # A failed preset restoration may be retried after the profile succeeds.
    preset = sc.sticky.get(PRESET_KEY)
    if preset is not None and preset.get("profile_before") == state["after"]:
        preset["profile_before"] = state["before"]
    if os.path.exists(backup):
        os.remove(backup)
    sc.sticky.pop(GENERAL_KEY, None)
    return {"general_restored": True}
