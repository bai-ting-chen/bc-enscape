# Troubleshooting

| Symptom | Action |
|---|---|
| Start, preparation or capture not done after 120 s, or an error | Stop polling; read `enscape.status()` and the host's Enscape logs. Never resubmit blindly. |
| Startup stalls on a cached GPU-query timeout | Restart only that idle renderer host. Never end Rhino or another session's host. |
| A window flashes at startup | Expected; the script hides it within 100 ms. |
| `restore_settings` refuses the profile backup | The profile changed outside the job; the backup is kept. Tell the user; never overwrite their change. |
| `apply_defaults` names a sheet line or a missing key | The sheet was edited wrongly: report the line; render nothing until it is fixed. |
| Internal export error at capture | The view reference is missing: run `prepare_capture` before `request_capture`. |
| `prepare_capture` refuses clipping planes | Clipped views are not supported yet. |
| Overlay problems | [image-overlay.md](image-overlay.md) › Rules |
