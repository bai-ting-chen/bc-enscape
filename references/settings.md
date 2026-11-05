# Settings

- **Sheet:** [render-defaults.md](../render-defaults.md) is the user's sheet. Every render starts
  from it. A value the user names overrides that setting for that render only. Change the sheet
  only when the user asks.
- **Schema:** `enscape.settings_schema()` lists every setting of the installed Enscape: key, type,
  bounds or enum names, Enscape default and current value. Use it to name a setting the user
  describes in words. Pass enum names, not numbers.
- **Sources:** camera position from the Rhino named view, sun position from the Rhino sun, the rest
  from the sheet and the request.
- **User-wide group:** Rendering (raytracing, DLSS, denoisers) lives in the user's Enscape profile
  and is read when the renderer host starts. A running host keeps its old values, even through
  `start_hidden()`: to change them, restart only the bound `Enscape.RendererHost`.
- **Restore:** `enscape.restore_settings()` restores the document presets and the profile. The
  profile backup is in `%LOCALAPPDATA%\bc-enscape\` and survives a Rhino restart; while it exists,
  `check_version()` reports `pending_profile_restore` true. It raises one error naming every
  restore whose readback differs.
- **Refusals:** `apply_defaults` naming a sheet line or a missing key means the sheet was edited
  wrongly: report the line and render nothing until it is fixed. `restore_settings` refusing the
  profile backup means the profile changed outside the job: the backup is kept; tell the user.
- **Untested:** video and panorama settings are in the sheet but untested.
