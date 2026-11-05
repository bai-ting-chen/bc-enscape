"""Apply and restore temporary embedded visual presets."""
import System
import json
import scriptcontext as sc
from enscape_bridge import (
    ACTIVE_PRESET_ID,
    KEY,
    PRESET_KEY,
    _preset_records_match,
    embedded_presets,
    find_type,
    set_preset_data,
    typed_array,
)
from enscape_profile import profile_bytes
from enscape_settings import resolved_settings, validate_settings


def configure_preset(settings):
    """Overlay validated keys on the resolved preset and retain exact backups."""
    if PRESET_KEY in sc.sticky:
        raise ValueError("A temporary preset is already active; restore it first")
    if KEY in sc.sticky and "capture_task" in sc.sticky[KEY]:
        raise ValueError("Collect the pending capture before changing settings")
    changes = validate_settings(settings)
    resolved = resolved_settings()["visual"]
    options = {entry["key"]: {"Value": entry["value"]} for entry in resolved}
    options.update({key: {"Value": value} for key, value in changes.items()})
    store, document = embedded_presets()
    before = [(item.Identifier, item.Data) for item in store.GetAllStoredData(document)]
    preset_id = System.Guid.NewGuid()
    identifier_type = find_type("Enscape.Cad.EmbeddedData.DataObjectIdentifier")

    def identifier(value):
        return System.Activator.CreateInstance(identifier_type, System.Array[System.Object]([System.Guid(str(value)), ""]))

    payload = json.dumps({"Name": "background-temporary", "Settings": {"Version": 3, "Settings": options}})
    active = json.dumps({"Name": "ActivePresetId", "Settings": {"Version": 3,
                        "Settings": {"ActivePresetSetting": {"Value": str(preset_id)}}}})
    active_keys = [key for key, _ in before if str(key.Id) == ACTIVE_PRESET_ID]
    if not active_keys:
        active_keys = [identifier(ACTIVE_PRESET_ID)]
    records = [(identifier(preset_id), payload)] + [(key, active) for key in active_keys]
    sc.sticky[PRESET_KEY] = {"document": document, "store": store,
                             "before": before, "profile_before": profile_bytes(),
                             "written_keys": [key for key, _ in records]}
    try:
        set_preset_data(store, document, records)
        current = [(item.Identifier, item.Data) for item in store.GetAllStoredData(document)]
        if any(not any(key.Equals(old) and value == data for old, data in current)
               for key, value in records):
            raise ValueError("Preset write failed readback")
        if profile_bytes() != sc.sticky[PRESET_KEY]["profile_before"]:
            raise ValueError("User profile changed during preset configuration")
    except Exception:
        restore_preset()
        raise
    return {"changed": settings, "preset_id": str(preset_id), "stored_readback": True}


def restore_preset():
    state = sc.sticky[PRESET_KEY]
    before = state["before"]
    operations = []
    for key in state["written_keys"]:
        if not any(key.Equals(previous) for previous, _ in before):
            operation = System.Activator.CreateInstance(find_type("Enscape.Cad.EmbeddedData.DeleteEmbeddedDataOperation"))
            operation.Identifier = key
            operations.append(operation)
    if operations:
        state["store"].ExecuteOperations(state["document"], typed_array("Enscape.Cad.EmbeddedData.CadEmbeddedDataOperation", operations))
    if before:
        set_preset_data(state["store"], state["document"], before)
    current = [(item.Identifier, item.Data) for item in state["store"].GetAllStoredData(state["document"])]
    if not _preset_records_match(current, before):
        raise ValueError("Preset restoration failed readback")
    if profile_bytes() != state["profile_before"]:
        raise ValueError("User profile differs after preset restoration")
    del sc.sticky[PRESET_KEY]
    return {"preset_restored": True}
