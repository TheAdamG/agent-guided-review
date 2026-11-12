"""Harness-neutral CLI. There are deliberately no write, stage or commit actions."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .editor import discover, select_server
from .git import ReviewError, digest, inventory, previous_content, repository, safe_path
from .session import locked_session, make_plan, refresh, revisions, summary


def parser():
    p = argparse.ArgumentParser(prog="nvim-review", description="Agent-guided review in an existing Neovim; Git/index are read-only.")
    p.add_argument("--repo", default=".", help="Worktree (default: current directory)")
    p.add_argument("--server", help="Explicit Neovim socket; required if discovery is ambiguous")
    p.add_argument("--owner", default=os.environ.get("NVIM_REVIEW_OWNER") or os.environ.get("HC_TERMINAL_ID"),
                   help="Unique review controller (default: NVIM_REVIEW_OWNER or HC_TERMINAL_ID)")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("editors", help="List registered editors without opening or focusing anything")
    commands.add_parser("inventory", help="Snapshot staged, unstaged, untracked and unsaved changes")
    commands.add_parser("start", help="Start a new review; refuses to overwrite an existing review")
    resume = commands.add_parser("resume", help="Resume persisted review decisions")
    resume.add_argument("--take-over", action="store_true", help="Explicitly transfer controller/editor ownership")
    plan = commands.add_parser("plan", help="Install/revise semantic stops; preserves unchanged review decisions")
    plan.add_argument("--file", required=True, help="JSON plan, or - for stdin")
    show = commands.add_parser("show", help="Navigate to a stop without marking it reviewed")
    show.add_argument("id", nargs="?")
    show.add_argument("--location", type=int, default=1, help="1-based location within the stop")
    commands.add_parser("next", help="On user request: mark current stop reviewed and show the next pending stop")
    skip = commands.add_parser("skip", help="Leave current stop unresolved and continue")
    skip.add_argument("--reason", default="User deferred this topic")
    commands.add_parser("status", help="Refresh progress, stale decisions and unclassified changes")
    commands.add_parser("summary", help="Final honest coverage and manual-staging readiness")
    commands.add_parser("context", help="Read the current editor location and surrounding (possibly unsaved) source")
    commands.add_parser("preview", help="Show the existing floating hunk preview, or previous-source fallback")
    visit = commands.add_parser("visit", help="Detour to supporting source without changing the current stop")
    visit.add_argument("path")
    visit.add_argument("line", type=int)
    commands.add_parser("release", help="Release navigation ownership; keep persisted progress")
    return p


def locations(state):
    return [p["path"] for stop in state["stops"] for p in stop["locations"]]


def buffer_revision(root, snapshot):
    return digest({b["path"]: b["hash"] for b in snapshot.get("buffers", [])
                   if b["modified"] and Path(b["path"]).is_relative_to(root)})


def capture(root, editor, extra_paths=()):
    before = editor.call("snapshot")
    result = inventory(root, before, extra_paths)
    revision = buffer_revision(root, before)
    if revision != buffer_revision(root, editor.call("snapshot")):
        raise ReviewError("Editor buffers changed during inventory. Wait for the user before proceeding")
    result["editor_revision"] = revision
    return result


def current_stop(state, identifier=None):
    identifier = identifier or state["current"]
    stop = next((s for s in state["stops"] if s["id"] == identifier), None)
    if stop is None:
        raise ReviewError("Choose a stop with show ID; there is no current stop")
    return stop


def publish(editor, state):
    editor.call("progress", repo=state["repo"], owner=state["owner"], progress={
        "current": state["current"], "reviewed": sum(s["status"] == "reviewed" for s in state["stops"]),
        "total": len(state["stops"]), "title": next((s["title"] for s in state["stops"] if s["id"] == state["current"]), ""),
    })


def check_source(target):
    if target.is_dir():
        raise ReviewError("Directories/submodules need discussion rather than source navigation")
    if target.exists():
        if target.stat().st_size > 1024 * 1024:
            raise ReviewError("Source is larger than 1 MiB; discuss it without opening it")
        if b"\0" in target.read_bytes():
            raise ReviewError("Binary file; discuss it without opening it as source")


def show(editor, root, state, snapshot, stop, location_number=1):
    if stop["revision"] != revisions(stop, snapshot):
        raise ReviewError(f"Stop {stop['id']} changed since planning. Read context/inventory and revise its locations with plan")
    if not 1 <= location_number <= len(stop["locations"]):
        raise ReviewError("Location is outside this stop's location list")
    location = stop["locations"][location_number - 1]
    target = safe_path(root, location["path"])
    check_source(target)
    historical = previous_content(root, location["path"]) if not target.exists() else None
    result = editor.call("show", repo=str(root), owner=state["owner"], **location, historical=historical)
    # Recheck after buffer load/checktime and its user autocmds. Never certify
    # that a stale location was shown if loading itself changed the content.
    after = capture(root, editor, locations(state))
    if stop["revision"] != revisions(stop, after):
        stop["status"] = "changed"
        stop.pop("shown_revision", None)
        raise ReviewError("Source changed while opening it. Refresh inventory and revise the stop")
    stop["shown_revision"] = revisions(stop, after)
    state["current"] = stop["id"]
    publish(editor, state)
    return {"stop": stop, "location": result, "instruction": "Explain this stop in the agent pane, then wait for the user."}


def run(args):
    if args.command == "editors":
        return {"editors": discover()}
    root = repository(args.repo)
    editor = select_server(root, args.server)
    if args.command == "context":
        return editor.call("context", repo=str(root))
    if args.command == "inventory":
        return capture(root, editor)
    read_only = args.command in ("status", "summary")
    if not read_only and not args.owner:
        raise ReviewError("Set --owner to a unique agent/session ID (or use a Herdr agent terminal)")
    document = None
    if args.command == "plan":
        document = json.load(sys.stdin) if args.file == "-" else json.loads(Path(args.file).read_text())
        if not isinstance(document, dict) or not isinstance(document.get("stops"), list):
            raise ReviewError("Plan requires a JSON object with a stops array")
        if any(not isinstance(s, dict) or not isinstance(s.get("locations"), list)
               or any(not isinstance(p, dict) or not isinstance(p.get("path"), str) for p in s["locations"])
               for s in document["stops"]):
            raise ReviewError("Each stop needs a locations array of objects with string paths")
        if not isinstance(document.get("omitted", []), list) or any(not isinstance(o, dict) for o in document.get("omitted", [])):
            raise ReviewError("Omitted must be an array of objects")
    with locked_session(root) as (state, save):
        if args.command == "start":
            if state:
                raise ReviewError("A saved review exists. Use resume, then plan to revise it; progress was not discarded")
            editor.call("claim", repo=str(root), owner=args.owner)
            state = {"version": 1, "repo": str(root), "owner": args.owner, "server": editor.server,
                     "planned": False, "stops": [], "omitted": [], "current": None}
            state = refresh(state, capture(root, editor))
            save(state)
            return summary(state)
        if state is None:
            raise ReviewError("No saved review. Start with nvim-review start")
        if args.command == "resume":
            other_owner = state["owner"] not in (None, args.owner)
            other_editor = state["server"] != editor.server
            if (other_owner or other_editor) and not args.take_over:
                raise ReviewError("Review belongs to a different controller/editor. Ask the user before resume --take-over")
            editor.call("claim", repo=str(root), owner=args.owner, take_over=args.take_over)
            state.update(owner=args.owner, server=editor.server)
        elif not read_only and (state["owner"] != args.owner or state["server"] != editor.server):
            raise ReviewError("This controller/editor does not own the review. Resume it explicitly")
        elif state["server"] != editor.server:
            raise ReviewError("Review is attached to a different editor; select its --server or explicitly resume here")
        extras = locations(state)
        if document:
            extras += [location["path"] for stop in document.get("stops", []) for location in stop.get("locations", [])]
        snapshot = capture(root, editor, extras)
        refresh(state, snapshot)
        # Persist invalidations even if the requested action is subsequently refused.
        save(state)
        if args.command in ("status", "summary"):
            return summary(state)
        if args.command == "resume":
            publish(editor, state)
            save(state)
            return summary(state)
        if args.command == "release":
            editor.call("release", repo=str(root), owner=args.owner)
            state["owner"] = None
            save(state)
            return {"released": True, "progress_preserved": True}
        if args.command == "plan":
            state = make_plan(root, document, snapshot, state)
            # Claim also verifies the editor-side owner before installing progress.
            editor.call("claim", repo=str(root), owner=args.owner)
            publish(editor, state)
            save(state)
            return summary(state)
        if args.command == "visit":
            target = safe_path(root, args.path)
            if not target.is_file() or args.line < 1:
                raise ReviewError("Visit requires an existing source file and a positive line")
            check_source(target)
            return editor.call("show", repo=str(root), owner=args.owner, path=args.path, line=args.line)
        if args.command == "preview":
            context = editor.call("context", repo=str(root))
            path, line = context["path"], context["line"]
            old = previous_content(root, path)
            info = next((f for f in snapshot["files"] if f["path"] == path), None)
            if info and not info["unstaged"]["hunks"] and info["staged"]["hunks"]:
                from .git import git
                old = git(root, "show", f"HEAD:{path}", optional=True).decode(errors="replace").splitlines()
            hunks = (info["unstaged"]["hunks"] or info["staged"]["hunks"]) if info else []
            hunk = next((h for h in hunks if h["line"] <= line <= h["line"] + max(0, h["count"] - 1)), None)
            old_line = hunk["old_line"] if hunk else line
            start = max(0, old_line - 6)
            before = old[start:min(len(old), max(start + 12, old_line + (hunk["old_count"] if hunk else 5)))][:120]
            return editor.call("preview", repo=str(root), owner=args.owner, before=before or ["(No previous source at this location)"])
        if args.command == "show":
            stop = current_stop(state, args.id) if args.id or state["current"] else next(
                (s for s in state["stops"] if s["status"] == "pending"), None)
            if stop is None:
                return summary(state)
            result = show(editor, root, state, snapshot, stop, args.location)
            save(state)
            return result
        if args.command in ("next", "skip"):
            checked = editor.call("check", repo=str(root), owner=args.owner)
            if buffer_revision(root, checked) != snapshot["editor_revision"]:
                raise ReviewError("Editor changed before approval. Refresh and review the updated source")
            stop = current_stop(state)
            if stop.get("shown_revision") != revisions(stop, snapshot):
                raise ReviewError("Current stop has not been shown at this revision. Show/replan it before advancing")
            stop["status"] = "reviewed" if args.command == "next" else "unresolved"
            if args.command == "skip":
                stop["reason"] = args.reason
            stop.pop("shown_revision", None)
            position = state["stops"].index(stop)
            following = next((s for s in state["stops"][position + 1:] if s["status"] != "reviewed"), None)
            # Save the user's decision before trying navigation, which may safely refuse.
            state["current"] = None
            save(state)
            if following:
                result = show(editor, root, state, snapshot, following)
                save(state)
                return result
            publish(editor, state)
            return summary(state)
        raise ReviewError("Unsupported action")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run(args)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=True, indent=2))
        return 0
    except (ReviewError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 1
