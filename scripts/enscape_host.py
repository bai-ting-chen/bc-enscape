"""Restart only the bound Rhino's renderer host and let Enscape reconnect it."""
import hashlib
import scriptcontext as sc
from enscape_bridge import FLAGS, KEY, find_type, service_provider, services
from enscape_profile import profile_bytes
from enscape_job import JOB_KEY
from enscape_renderer import _renderer_process, _renderer_processes, close_renderer, start_hidden, status


_RESTART_KEY = "bc_enscape_host_restart"


def restart_renderer_host():
    """Poll this call until complete; a completed result is retained for this profile.

    Close job-owned rendering, terminate its idle host, await Enscape's native
    reconnect supervisor, then start hidden. No sleeps, waits or shell launches.
    Changing the profile starts a new restart; an unchanged completed call is a poll.
    """
    profile = profile_bytes()
    state = sc.sticky.get(_RESTART_KEY)
    if state and state["document"] != sc.doc.RuntimeSerialNumber:
        raise ValueError("The bound document changed during host restart")
    if not state or (state["phase"] == "complete" and (
            state["profile"] != profile or state.get("job") is not sc.sticky.get(JOB_KEY))):
        process = _renderer_process()
        active = sc.sticky.get(KEY, {})
        if services()["state"].CurrentState.IsRunning and not active.get("started_by_job"):
            raise ValueError("Refusing to restart a renderer this job did not start")
        if "capture_task" in active:
            raise ValueError("Collect the pending capture before restarting its host")
        state = {"document": sc.doc.RuntimeSerialNumber, "profile": profile,
                 "job": sc.sticky.get(JOB_KEY),
                 "old_process": process, "old_pid": process.Id, "phase": "close"}
        sc.sticky[_RESTART_KEY] = state
    if profile != state["profile"]:
        raise ValueError("Profile changed during host restart; refusing mixed settings")
    if state["phase"] == "close":
        if close_renderer()["complete"]:
            state["phase"] = "terminate"
    elif state["phase"] == "terminate":
        process = _renderer_process()
        if process.Id != state["old_pid"] or state["old_process"].HasExited:
            raise ValueError("Renderer host identity changed before termination")
        if services()["state"].CurrentState.IsRunning:
            raise ValueError("Renderer must be stopped before its host is terminated")
        # Enscape's RemoteCadConsumer supervises this exact child process and
        # reconnects it through its own dispatcher after an unexpected exit.
        process.Kill()
        state["phase"] = "reconnect"
    elif state["phase"] == "reconnect":
        if not state["old_process"].HasExited:
            return _result(state)
        matches = _renderer_processes()
        if not matches:
            return _result(state)
        if len(matches) != 1:
            raise ValueError("Ambiguous renderer hosts during reconnect")
        process = matches[0]
        if process.Id == state["old_pid"]:
            raise RuntimeError("Restart did not replace the renderer host")
        consumer = service_provider().GetService(find_type("Enscape.Cad.Remote.RemoteCadConsumer"))
        connection = consumer.GetType().GetField("_connection", FLAGS).GetValue(consumer)
        if connection is not None:
            state["new_pid"] = process.Id
            state["phase"] = "start"
    elif state["phase"] == "start":
        start_hidden()
        state["job"] = sc.sticky.get(JOB_KEY)
        state["phase"] = "starting"
    elif state["phase"] == "starting":
        ready = status()
        if ready["error"]:
            raise RuntimeError(ready["error"])
        if ready["running"] and ready["start_task"] == "RanToCompletion":
            state["phase"] = "complete"
    return _result(state)


def _result(state):
    return {"complete": state["phase"] == "complete", "phase": state["phase"],
            "old_process_id": state["old_pid"], "new_process_id": state.get("new_pid"),
            "profile_sha256": hashlib.sha256(state["profile"] or b"").hexdigest()}
