"""Access Enscape plug-in services and embedded-data primitives."""
import Rhino
import System


KEY = "bc_enscape_background"
PRESET_KEY = "bc_enscape_temporary_preset"
ACTIVE_PRESET_ID = "d8e4b713-533b-421b-8b11-a961385e4b37"
FLAGS = System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance


def find_type(name):
    return next(assembly.GetType(name)
                for assembly in System.AppDomain.CurrentDomain.GetAssemblies()
                if assembly.GetType(name) is not None)


def service_provider():
    plugin_type = find_type("Enscape.Rhino.Plugin.EnscapeRhinoPlugin")
    plugin_id = plugin_type.GetProperty("PluginGuidIdentifier").GetValue(None)
    plugin = Rhino.PlugIns.PlugIn.Find(plugin_id)
    return plugin_type.GetField("_serviceProvider", FLAGS).GetValue(plugin)


def services():
    provider = service_provider()
    return {
        "sync": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RenderingSync.RhinoRenderingSync")),
        "state": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RenderingSync.CurrentRenderingStateProvider")),
        "screenshots": provider.GetService(find_type(
            "Enscape.Rhino.Plugin.RhinoStoredRenderings")),
    }


def typed_array(type_name, values):
    array = System.Array.CreateInstance(find_type(type_name), len(values))
    for index, value in enumerate(values):
        array.SetValue(value, index)
    return array


def embedded_presets():
    provider = service_provider()
    embedded = provider.GetService(find_type("Enscape.Rhino.Plugin.EmbeddedData.RhinoEmbeddedData"))
    sync = services()["sync"]
    document = sync.GetType().GetField("_documents", FLAGS).GetValue(sync).ActiveDocument
    return embedded.VisualSettingPresets, document


def set_preset_data(store, document, records):
    operations = []
    for identifier, payload in records:
        data = System.Activator.CreateInstance(find_type("Enscape.Cad.EmbeddedData.DataObject"),
                                              System.Array[System.Object]([identifier, payload]))
        operation = System.Activator.CreateInstance(find_type("Enscape.Cad.EmbeddedData.SetEmbeddedDataOperation"))
        operation.DataObject = data
        operations.append(operation)
    store.ExecuteOperations(document, typed_array("Enscape.Cad.EmbeddedData.CadEmbeddedDataOperation", operations))


def _preset_records_match(current, before):
    return len(current) == len(before) and all(
        any(key.Equals(old) and value == data for old, data in before) for key, value in current)
