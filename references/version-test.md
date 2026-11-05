# Version test

Enscape updates often, and an update closes Rhino. Before the first render after a start,
`enscape.check_version()` compares the installed version with the line in [SKILL.md](../SKILL.md).
If they differ, run this test before rendering. Pass: update that line and commit. Fail: report
what broke and render nothing.

1. On a scratch scene unless the user names a file: one 3 m box (real scale) and one named view.
2. SKILL.md steps 2–4.
3. Call `enscape.version_test(absolute_new_output_folder)` in later Rook calls, with the same folder,
   until it returns `complete` true. It checks the sheet against the schema, sets, reads back and
   restores one setting per category, and makes one capture.
4. Look at the capture.
