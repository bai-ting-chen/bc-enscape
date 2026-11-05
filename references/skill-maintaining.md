# Maintaining the skill

This folder is the Git repo `github.com/bai-ting-chen/bc-enscape` (private), shared by several
machines; Codex and Claude Code share it.

1. Before editing: `git pull --ff-only origin main`. If it refuses, stop and report; never
   force-push.
2. Identity, set once per clone: `git config user.name bai-ting-chen`,
   `git config user.email baitingchen.arch@gmail.com`.
3. After a change: commit (what changed and why), then `git push origin main`.
4. Only method and code go in: no project files, renders or values from one scene (sizes, sun
   angles, view names). House defaults live only in [render-defaults.md](../render-defaults.md).
5. A reference that documents one script shares its name (`image-overlay.md` ↔ `image_overlay.py`).
