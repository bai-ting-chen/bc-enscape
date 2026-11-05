"""Preserve and recover exact on-disk Enscape profile bytes."""
import System
import base64
import json
import os


def profile_path():
    return os.path.join(str(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments)),
                        "Enscape", "Settings", "settings.json")


def profile_bytes():
    path = profile_path()
    if not os.path.exists(path):
        return None
    with open(path, "rb") as stream:
        return stream.read()


def _profile_backup_path():
    return os.path.join(os.environ["LOCALAPPDATA"], "bc-enscape", "profile-backup.json")


def _read_profile_backup():
    with open(_profile_backup_path(), "rb") as stream:
        data = json.loads(stream.read().decode("utf-8"))
    if os.path.normcase(os.path.abspath(data["profile_path"])) != os.path.normcase(os.path.abspath(profile_path())):
        raise ValueError("Profile backup belongs to a different profile path; backup retained")
    return {key: base64.b64decode(data[key], validate=True) if data[key] is not None else None
            for key in ("before", "after")}


def _write_profile_backup(before, after):
    """Persist exact recovery bytes before any profile replacement; never overwrite."""
    path = _profile_backup_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {key: base64.b64encode(value).decode("ascii") if value is not None else None
            for key, value in (("before", before), ("after", after))}
    data["profile_path"] = os.path.abspath(profile_path())
    created = False
    try:
        with open(path, "xb") as stream:
            created = True
            stream.write(json.dumps(data).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if _read_profile_backup() != {"before": before, "after": after}:
            raise ValueError("Profile backup failed byte readback")
    except Exception:
        if created:
            os.remove(path)
        raise


def write_profile(data):
    """Replace the profile only after the complete new contents reach disk."""
    path = profile_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    staging = os.path.join(os.path.dirname(path), "bc-enscape-settings-" + str(System.Guid.NewGuid()) + ".tmp")
    try:
        with open(staging, "xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        if os.path.exists(staging):
            os.remove(staging)
