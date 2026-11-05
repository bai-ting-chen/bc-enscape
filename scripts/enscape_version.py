"""Compare installed Enscape with the documented tested version."""
import os
import re
from enscape_bridge import find_type
from enscape_profile import _profile_backup_path
from enscape_sheet import _skill_file


def _installed_version():
    return str(find_type("Enscape.Rhino.Plugin.EnscapeRhinoPlugin").Assembly.GetName().Version)


def check_version():
    """Report the installed and documented versions; a mismatch is not an error."""
    with open(_skill_file("SKILL.md"), encoding="utf-8-sig") as stream:
        matches = re.findall(r"^Tested with Enscape (\d+(?:\.\d+){1,3})\.\s*$", stream.read(), re.MULTILINE)
    tested = matches[0] if len(matches) == 1 else None
    installed = _installed_version()
    return {"installed": installed, "tested": tested, "match": installed == tested,
            "pending_profile_restore": os.path.exists(_profile_backup_path())}
