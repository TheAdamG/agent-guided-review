"""A JSON-only interface over Neovim's existing local RPC socket."""

import json
import os
import subprocess
from pathlib import Path

from .git import ReviewError


def state_home():
    return Path(os.environ.get("NVIM_REVIEW_STATE_DIR", Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "nvim-guided-review"))


def with_messages(error, messages):
    if not messages:
        return str(error)
    return str(error) + "\nCaptured Neovim messages:\n" + "\n".join(
        f"[{m['path']}: {m['phase']}]\n{m['text']}" for m in messages
    )


class Editor:
    def __init__(self, server):
        self.server = server

    def call(self, action, **arguments):
        payload = json.dumps({"action": action, **arguments}, ensure_ascii=True)
        # The only evaluated expression is fixed code plus a quoted JSON string.
        # File names and agent-provided titles never become Lua/Vim commands.
        quoted = "'" + payload.replace("'", "''") + "'"
        expr = f"luaeval(\"require('guided-review').rpc(_A)\", {quoted})"
        try:
            result = subprocess.run(
                ["nvim", "--server", self.server, "--remote-expr", expr],
                capture_output=True, text=True, timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReviewError(f"Cannot reach Neovim at {self.server}: {exc}") from exc
        if result.returncode:
            raise ReviewError(result.stderr.strip() or "Neovim RPC failed; load guided-review in that editor")
        try:
            reply = json.loads(result.stdout)
        except ValueError as exc:
            raise ReviewError(f"Invalid editor response: {result.stdout[:200]}") from exc
        if not reply.get("ok"):
            raise ReviewError(with_messages(reply.get("error", "Neovim refused the request"), reply.get("messages")))
        return reply["result"]


def discover():
    records = []
    for file in sorted((state_home() / "editors").glob("*.json")):
        try:
            entry = json.loads(file.read_text())
            os.kill(entry["pid"], 0)
            records.append(entry)
        except (OSError, ValueError, KeyError):
            continue
    return records


def select_server(root, server=None):
    if server:
        return Editor(server)
    candidates = [r for r in discover() if str(root) in r.get("repos", [])]
    # Within Herdr, prefer an editor belonging to this agent's exact tab.
    terminal = os.environ.get("HC_TERMINAL_ID")
    if len(candidates) > 1 and terminal:
        try:
            facts = subprocess.run(["hc", "get", terminal, "--json"], capture_output=True, text=True, timeout=2, check=False)
            labels = json.loads(facts.stdout).get("labels", {})
            peers = [r for r in candidates if r.get("labels") == {
                "space": labels.get("herdr.space"), "tab": labels.get("herdr.tab"),
            } and labels.get("herdr.tab")]
            if peers:
                candidates = peers
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    if len(candidates) != 1:
        raise ReviewError("No unique editor for this repository. Run nvim-review editors and choose --server SOCKET. "
                          "In an existing Neovim, load :lua require('guided-review').setup()")
    return Editor(candidates[0]["server"])
