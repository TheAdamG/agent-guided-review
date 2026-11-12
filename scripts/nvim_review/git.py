"""Read Git without touching the index or running external diff drivers."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


class ReviewError(Exception):
    pass


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=True).encode()
    return hashlib.sha256(value).hexdigest()


def git(root, *args, optional=False):
    result = subprocess.run(
        ["git", "--no-optional-locks", "--literal-pathspecs", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        capture_output=True, timeout=20, check=False,
    )
    if result.returncode and not optional:
        raise ReviewError(result.stderr.decode(errors="replace").strip() or "Git command failed")
    return result.stdout if not result.returncode else b""


def repository(path):
    return Path(os.fsdecode(git(path, "rev-parse", "--show-toplevel")).rstrip("\n")).resolve()


def safe_path(root, path):
    """Allow missing/deleted paths, but never escape the worktree via a symlink."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ReviewError("Expected a nonempty repository-relative file path")
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
        raise ReviewError(f"Unsafe review path: {path!r}")
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise ReviewError(f"Path escapes the repository through a symlink: {path!r}")
    return target


def disk_hash(path):
    if path.is_symlink():
        return digest(os.fsencode(os.readlink(path)))
    if not path.exists():
        return "missing"
    if not path.is_file():
        return "directory"
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(131072), b""):
            h.update(chunk)
    return h.hexdigest()


def file_info(root, path, status, buffers):
    # Inventory symlinks without following their targets. Navigation separately
    # uses safe_path, so an outside symlink can be explained but not opened.
    target = root / path
    before = disk_hash(target)
    diffs = {}
    for layer, flags in (("staged", ["--cached"]), ("unstaged", [])):
        patch = git(root, "diff", *flags, "--no-ext-diff", "--no-textconv", "--no-renames", "--unified=0", "--", path)
        hunks = []
        for match in re.finditer(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@([^\n]*)", patch, re.MULTILINE):
            old, old_count, new, new_count, heading = match.groups()
            hunks.append({
                "old_line": int(old), "old_count": int(old_count or 1),
                "line": max(1, int(new)), "count": int(new_count or 1),
                "heading": heading.decode(errors="replace").strip(),
            })
        diffs[layer] = {"hash": digest(patch), "hunks": hunks, "binary": b"Binary files " in patch}
    buffer = buffers.get(str(target))
    disk = disk_hash(target)
    if disk != before:
        raise ReviewError(f"File changed during inventory: {path!r}. Retry when editing has settled")
    signature = digest({
        "disk": disk, "staged": diffs["staged"]["hash"], "unstaged": diffs["unstaged"]["hash"],
        "buffer": buffer["hash"] if buffer and buffer["modified"] else None,
    })
    return {
        "path": path, "status": status, "revision": signature, "disk_hash": disk,
        "kind": "symlink" if target.is_symlink() else ("directory" if target.is_dir() else "file"),
        "missing": not target.exists(), "unsaved": bool(buffer and buffer["modified"]),
        **diffs,
    }


def inventory(root, editor_snapshot, extra_paths=()):
    head = git(root, "rev-parse", "--verify", "HEAD", optional=True).decode().strip() or "unborn"
    index_path = Path(os.fsdecode(git(root, "rev-parse", "--git-path", "index")).rstrip("\n"))
    if not index_path.is_absolute():
        index_path = root / index_path
    index_before = disk_hash(index_path)
    raw = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    statuses = {}
    for item in raw.split(b"\0"):
        if item:
            statuses[os.fsdecode(item[3:])] = item[:2].decode()
    buffers = {b["path"]: b for b in editor_snapshot.get("buffers", [])}
    for path, buffer in buffers.items():
        target = Path(path)
        if buffer["modified"] and target.is_relative_to(root) and ".git" not in target.relative_to(root).parts:
            statuses.setdefault(str(target.relative_to(root)), " B")
    files = {path: file_info(root, path, status, buffers) for path, status in sorted(statuses.items())}
    token = digest({"head": head, "files": {p: f["revision"] for p, f in files.items()}})
    supporting = {}
    for path in sorted(set(extra_paths) - files.keys()):
        safe_path(root, path)
        supporting[path] = file_info(root, path, "  ", buffers)
    final_head = git(root, "rev-parse", "--verify", "HEAD", optional=True).decode().strip() or "unborn"
    final_status = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    if head != final_head or raw != final_status or index_before != disk_hash(index_path):
        raise ReviewError("Git changed during inventory. Retry when concurrent changes have settled")
    for path, info in {**files, **supporting}.items():
        if info["disk_hash"] != disk_hash(root / path):
            raise ReviewError(f"File changed during inventory: {path!r}. Refresh before proceeding")
    return {"repo": str(root), "head": head, "snapshot": token, "files": list(files.values()), "supporting": supporting}


def previous_content(root, path):
    safe_path(root, path)
    indexed = bool(git(root, "ls-files", "--stage", "--", path))
    content = git(root, "show", f":{path}" if indexed else f"HEAD:{path}", optional=True)
    if len(content) > 1024 * 1024 or b"\0" in content:
        raise ReviewError("Historical file is binary or larger than 1 MiB; discuss it without opening it")
    return content.decode("utf-8", errors="replace").splitlines() or ([""] if indexed else [])
