"""Expose the stable Enscape helper API used by agents inside Rhino.

After replacing the former single-file helper, importlib.reload(enscape) loads
these new modules. Later edits to an already imported module require reloading
its dependencies first, then this facade.
"""
import ctypes
import base64
import json
import math
import os
import re
import shutil
import subprocess
import time
import Rhino
import System
import System.Windows.Forms as forms
import scriptcontext as sc

from enscape_bridge import (
    KEY,
    PRESET_KEY,
    ACTIVE_PRESET_ID,
    FLAGS,
    find_type,
    service_provider,
    services,
    typed_array,
    embedded_presets,
    set_preset_data,
    _preset_records_match,
)
from enscape_profile import (
    profile_path,
    profile_bytes,
    _profile_backup_path,
    _read_profile_backup,
    _write_profile_backup,
    write_profile,
)
from enscape_sheet import (
    SHEET_OMITTED_VISUAL_KEYS,
    _skill_file,
    _read_defaults,
)
from enscape_version import (
    _installed_version,
    check_version,
)
from enscape_settings import (
    SETTINGS_READER,
    CATEGORIES,
    BOUNDS,
    _key,
    resolved_settings,
    settings_schema,
    validate_settings,
)
from enscape_presets import (
    configure_preset,
    restore_preset,
)
from enscape_general import (
    GENERAL_KEY,
    configure_general,
    restore_general,
)
from enscape_defaults import (
    _check_sheet_complete,
    _split_settings,
    apply_defaults,
    lighting_overrides,
    restore_settings,
)
from enscape_renderer import (
    start_hidden,
    status,
    stop_monitor,
)
from enscape_capture import (
    prepare_capture,
    preparation_status,
    screenshot_files,
    request_capture,
    collect_capture,
)
from enscape_views import capture_views
from enscape_materials import (
    materials_schema,
    list_materials,
    read_material,
    validate_material,
    create_material,
    edit_material,
    refresh_materials,
)
from enscape_version_test import (
    VERSION_TEST_KEY,
    _probe_value,
    _version_category,
    _version_snapshot_matches,
    _version_capture,
    version_test,
)
