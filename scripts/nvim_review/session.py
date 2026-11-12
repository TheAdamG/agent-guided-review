"""Persistent review decisions. Shown is not reviewed; changed is not approved."""

import copy
import fcntl
import json
import os
from contextlib import contextmanager

from .editor import state_home
from .git import ReviewError, digest, safe_path


@contextmanager
def locked_session(root):
    directory = state_home() / "sessions"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (digest(str(root)) + ".json")
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else None
        yield state, lambda value: save(path, value)


def save(path, value):
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def all_files(snapshot):
    return {**snapshot.get("supporting", {}), **{f["path"]: f for f in snapshot["files"]}}


def revisions(stop, snapshot):
    files = all_files(snapshot)
    return {"head": snapshot["head"], "files": {p["path"]: files[p["path"]]["revision"] for p in stop["locations"]}}


def refresh(state, snapshot):
    for stop in state["stops"]:
        current = revisions(stop, snapshot)
        if stop["revision"] != current:
            stop["status"] = "changed"
            stop.pop("shown_revision", None)
    files = {f["path"]: f for f in snapshot["files"]}
    selected = {p["path"] for stop in state["stops"] for p in stop["locations"]}
    omitted = {o["path"]: o for o in state["omitted"]}
    state["unclassified"] = [
        path for path, file in files.items()
        if path not in selected and (path not in omitted or omitted[path]["revision"] != file["revision"])
    ]
    state["unsaved"] = [f["path"] for f in snapshot["files"] if f["unsaved"]]
    state["snapshot"] = snapshot["snapshot"]
    return state


def make_plan(root, document, snapshot, old):
    if document.get("snapshot") != snapshot["snapshot"]:
        raise ReviewError("Changes moved since the inventory. Refresh inventory and reconsider the plan")
    if not isinstance(document.get("stops"), list) or not isinstance(document.get("omitted", []), list):
        raise ReviewError("Plan requires stops and omitted arrays")
    result, ids = [], set()
    files = all_files(snapshot)
    previous = {s["id"]: s for s in old["stops"]}
    for proposed in document["stops"]:
        identifier = proposed.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in ids:
            raise ReviewError("Each stop requires a unique nonempty string id")
        ids.add(identifier)
        for key in ("title", "why"):
            if not isinstance(proposed.get(key), str) or not proposed[key].strip():
                raise ReviewError(f"Stop {identifier} requires {key}")
        locations = proposed.get("locations")
        if not isinstance(locations, list) or not locations:
            raise ReviewError(f"Stop {identifier} needs at least one location")
        locations = copy.deepcopy(locations)
        for location in locations:
            path = location.get("path")
            safe_path(root, path)
            if path not in files:
                raise ReviewError(f"No inventory for {path!r}")
            start, end = location.get("line"), location.get("end_line", location.get("line"))
            if type(start) is not int or type(end) is not int or start < 1 or end < start:
                raise ReviewError("Locations require positive line and optional end_line >= line")
            location["end_line"] = end
        stop = {"id": identifier, "title": proposed["title"], "why": proposed["why"],
                "locations": locations, "status": "pending"}
        stop["revision"] = revisions(stop, snapshot)
        prior = previous.get(identifier)
        if prior and all(prior.get(k) == stop.get(k) for k in ("title", "why", "locations", "revision")):
            stop["status"] = prior["status"]
            if "reason" in prior:
                stop["reason"] = prior["reason"]
        result.append(stop)
    omitted, seen = [], set()
    selected = {p["path"] for s in result for p in s["locations"]}
    for item in document.get("omitted", []):
        path, reason = item.get("path"), item.get("reason")
        if path not in files or path in seen or path in selected or not isinstance(reason, str) or not reason.strip():
            raise ReviewError("Each omitted file needs a unique unselected inventory path and a reason")
        seen.add(path)
        omitted.append({"path": path, "reason": reason, "revision": files[path]["revision"]})
    unclassified = {f["path"] for f in snapshot["files"]} - selected - seen
    if unclassified:
        raise ReviewError("Consider every changed file: select a stop or explain omission for " + repr(sorted(unclassified)))
    state = {**old, "stops": result, "omitted": omitted, "current": None, "planned": True}
    return refresh(state, snapshot)


def summary(state):
    reviewed = [s["id"] for s in state["stops"] if s["status"] == "reviewed"]
    unresolved = [{"id": s["id"], "title": s["title"], "status": s["status"]}
                  for s in state["stops"] if s["status"] != "reviewed"]
    ready = state["planned"] and not unresolved and not state.get("unclassified") and not state.get("unsaved")
    return {
        **state, "reviewed_count": len(reviewed), "total_stops": len(state["stops"]),
        "unresolved": unresolved, "ready_to_ask_about_staging": ready,
        "coverage": "Selected topics only; not every line or hunk was individually reviewed.",
        "staging": "Ask the user to stage files themselves." if ready else "Review is not complete.",
    }
