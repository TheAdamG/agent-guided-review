"""Run: python3 -m unittest discover -s tests -p test_guided_review.py -v"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from nvim_review.cli import capture
from nvim_review.git import (
    ReviewError,
    disk_hash,
    inventory,
    previous_content,
    repository,
    safe_path,
)
from nvim_review.session import make_plan, refresh, summary


class RepositoryCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="nvim-review-", dir=os.environ.get("NVIM_REVIEW_TEST_TMPDIR")
        )
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.original = "".join(f"original line {i}\n" for i in range(1, 31))
        (self.root / "source.txt").write_text(self.original)
        (self.root / "deleted.txt").write_text("deleted source\nsecond line\n")
        self.git("add", ".")
        self.git("-c", "user.name=Review Test", "-c", "user.email=review@example.invalid",
                 "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "baseline")
        changed = self.original.replace("original line 3\n", "new line 3\n").replace("original line 25\n", "new line 25\n")
        (self.root / "source.txt").write_text(changed)

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True).stdout

    def snapshot(self, **kwargs):
        return inventory(self.root, {"buffers": []}, **kwargs)

    def plan(self, snapshot, paths=None):
        paths = paths or ["source.txt"]
        return {
            "snapshot": snapshot["snapshot"],
            "stops": [{"id": "behavior", "title": "Explain new behavior", "why": "Changes user-visible behavior",
                       "locations": [{"path": path, "line": 3 if path == "source.txt" else 1} for path in paths]}],
            "omitted": [{"path": f["path"], "reason": "Routine supporting change"} for f in snapshot["files"] if f["path"] not in paths],
        }

    def state(self):
        return {"version": 1, "owner": "agent-a", "server": "socket", "repo": str(self.root),
                "planned": False, "stops": [], "omitted": [], "current": None}


class InventoryTests(RepositoryCase):
    def test_both_layers_new_deleted_binary_and_unusual_paths(self):
        self.git("add", "source.txt")
        (self.root / "source.txt").write_text((self.root / "source.txt").read_text() + "unstaged tail\n")
        (self.root / "deleted.txt").unlink()
        unusual = "space ' quote 中文\nfile.txt"
        (self.root / unusual).write_text("new source\n")
        (self.root / "binary.dat").write_bytes(b"\x00old\x01")
        self.git("add", "binary.dat")
        (self.root / "binary.dat").write_bytes(b"\x00new\x01")
        files = {f["path"]: f for f in self.snapshot()["files"]}
        self.assertIn(unusual, files)
        self.assertEqual(files[unusual]["status"], "??")
        self.assertTrue(files["deleted.txt"]["missing"])
        self.assertEqual(len(files["source.txt"]["staged"]["hunks"]), 2)
        self.assertTrue(files["source.txt"]["unstaged"]["hunks"])
        self.assertTrue(files["binary.dat"]["unstaged"]["binary"])

    def test_unsaved_clean_tracked_file_is_in_inventory(self):
        snap = inventory(self.root, {"buffers": [{"path": str(self.root / "deleted.txt"), "modified": True, "hash": "unsaved"}]})
        files = {f["path"]: f for f in snap["files"]}
        self.assertTrue(files["deleted.txt"]["unsaved"])
        self.assertEqual(files["deleted.txt"]["status"], " B")

    def test_paths_cannot_escape_or_open_git_internals(self):
        (self.root / "outside").symlink_to(self.directory)
        for path in ("../secret", "/etc/passwd", ".git/config", "outside/secret"):
            with self.subTest(path=path), self.assertRaises(ReviewError):
                safe_path(self.root, path)

    def test_worktree_root_and_unborn_head(self):
        subdir = self.root / "nested"
        subdir.mkdir()
        self.assertEqual(repository(subdir), self.root)
        empty = self.directory / "unborn"
        empty.mkdir()
        subprocess.run(["git", "init", "-q", str(empty)], check=True)
        (empty / "new.txt").write_text("hello\n")
        self.assertEqual(inventory(empty, {"buffers": []})["head"], "unborn")

    def test_literal_pathspecs_and_empty_index_content(self):
        literal = self.root / "literal[1].txt"
        neighbor = self.root / "literal1.txt"
        literal.write_text("old literal\n")
        neighbor.write_text("old neighbor\n")
        self.git("add", ".")
        literal.write_text("new literal\n")
        neighbor.write_text("new neighbor\n")
        files = {f["path"]: f for f in self.snapshot()["files"]}
        self.assertEqual(len(files[literal.name]["unstaged"]["hunks"]), 1)
        literal.write_text("")
        self.git("--literal-pathspecs", "add", literal.name)
        literal.write_text("new unstaged source\n")
        self.assertEqual(previous_content(self.root, literal.name), [""])

    def test_concurrent_disk_and_editor_changes_fail_closed(self):
        target = self.root / "source.txt"
        changed = False

        def racing_hash(path):
            nonlocal changed
            value = disk_hash(path)
            if path == target and not changed:
                changed = True
                target.write_text("concurrent change\n")
            return value

        with patch("nvim_review.git.disk_hash", side_effect=racing_hash), self.assertRaisesRegex(ReviewError, "during inventory"):
            self.snapshot()
        editor = Mock()
        editor.call.side_effect = [
            {"buffers": [{"path": str(target), "modified": True, "hash": "before"}]},
            {"buffers": [{"path": str(target), "modified": True, "hash": "after"}]},
        ]
        with self.assertRaisesRegex(ReviewError, "Editor buffers changed"):
            capture(self.root, editor)


class StateTests(RepositoryCase):
    def test_plan_is_selective_but_considers_all_files(self):
        (self.root / "generated.txt").write_text("generated\n")
        snap = self.snapshot()
        document = self.plan(snap)
        document["omitted"] = []
        with self.assertRaisesRegex(ReviewError, "Consider every changed file"):
            make_plan(self.root, document, snap, self.state())
        state = make_plan(self.root, self.plan(snap), snap, self.state())
        self.assertEqual(len(state["stops"]), 1)  # two source hunks, one meaningful stop
        self.assertFalse(summary(state)["ready_to_ask_about_staging"])

    def test_changed_topics_invalidate_but_unchanged_decisions_survive_replanning(self):
        snap = self.snapshot()
        state = make_plan(self.root, self.plan(snap), snap, self.state())
        state["stops"][0]["status"] = "reviewed"
        same = make_plan(self.root, self.plan(snap), snap, state)
        self.assertEqual(same["stops"][0]["status"], "reviewed")
        (self.root / "source.txt").write_text("different code\n")
        new = self.snapshot()
        refresh(state, new)
        self.assertEqual(state["stops"][0]["status"], "changed")
        revised = self.plan(new)
        revised["stops"][0]["locations"][0]["line"] = 1
        self.assertEqual(make_plan(self.root, revised, new, state)["stops"][0]["status"], "pending")

    def test_omitted_and_new_changes_are_reconsidered(self):
        (self.root / "generated.txt").write_text("generated\n")
        snap = self.snapshot()
        state = make_plan(self.root, self.plan(snap), snap, self.state())
        state["stops"][0]["status"] = "reviewed"
        self.assertTrue(summary(state)["ready_to_ask_about_staging"])
        (self.root / "generated.txt").write_text("different generated output\n")
        (self.root / "new.txt").write_text("new\n")
        refresh(state, self.snapshot())
        self.assertEqual(state["unclassified"], ["generated.txt", "new.txt"])
        self.assertFalse(summary(state)["ready_to_ask_about_staging"])

    def test_head_change_and_unsaved_edits_block_completion(self):
        snap = self.snapshot()
        state = make_plan(self.root, self.plan(snap), snap, self.state())
        state["stops"][0]["status"] = "reviewed"
        new = copy.deepcopy(snap)
        new["head"] = "another-head"
        refresh(state, new)
        self.assertEqual(state["stops"][0]["status"], "changed")
        state["stops"][0]["status"] = "reviewed"
        state["unsaved"] = ["source.txt"]
        self.assertFalse(summary(state)["ready_to_ask_about_staging"])

    def test_stale_plan_snapshot_refused(self):
        snap = self.snapshot()
        document = self.plan(snap)
        document["snapshot"] = "stale"
        with self.assertRaisesRegex(ReviewError, "moved"):
            make_plan(self.root, document, snap, self.state())


class EditorIntegrationTests(RepositoryCase):
    def setUp(self):
        super().setUp()
        self.environment = {
            **os.environ,
            "NVIM_REVIEW_STATE_DIR": str(self.directory / "state"),
            "NVIM_REVIEW_PLUGIN_DIR": str(ROOT),
        }
        self.environment.pop("HC_TERMINAL_ID", None)
        self.environment.pop("HC_SCOPE", None)
        self.socket = str(self.directory / "nvim.sock")
        self.log = (self.directory / "nvim.log").open("w+")
        self.process = subprocess.Popen(
            ["nvim", "--headless", "-u", "NONE", "-i", "NONE", "--listen", self.socket,
             "--cmd", "lua vim.opt.rtp:prepend(vim.env.NVIM_REVIEW_PLUGIN_DIR)",
             "-c", "lua require('guided-review').setup({quiet_ms=0})"],
            cwd=self.root, env=self.environment, stdout=self.log, stderr=self.log,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if Path(self.socket).exists() and list((self.directory / "state/editors").glob("*.json")):
                break
            if self.process.poll() is not None:
                self.log.seek(0)
                self.fail(self.log.read())
            time.sleep(0.01)
        else:
            self.fail("Neovim registration timed out")

    def tearDown(self):
        if hasattr(self, "process"):
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.log.close()
        super().tearDown()

    def command(self, *args, owner="agent-a", success=True, stdin=None, server=True):
        argv = [sys.executable, str(ROOT / "bin/nvim-review"), "--repo", str(self.root), "--owner", owner]
        if server:
            argv += ["--server", self.socket]
        response = subprocess.run(argv + list(args), env=self.environment, text=True, input=stdin, capture_output=True, timeout=15, check=False)
        if success:
            self.assertEqual(response.returncode, 0, response.stderr + response.stdout)
            return json.loads(response.stdout)["result"]
        self.assertNotEqual(response.returncode, 0, response.stdout)
        return json.loads(response.stderr)["error"]

    def lua(self, body):
        expression = "(function() " + body + " end)()"
        quoted = "'" + expression.replace("'", "''") + "'"
        result = subprocess.run(["nvim", "--server", self.socket, "--remote-expr", "luaeval(" + quoted + ")"],
                                env=self.environment, text=True, capture_output=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def install_plan(self, document=None):
        snap = self.command("inventory")
        self.command("start")
        self.command("plan", "--file", "-", stdin=json.dumps(document or self.plan(snap)))

    def test_full_walkthrough_navigation_preview_and_no_git_mutation(self):
        before = (self.root / ".git/index").read_bytes()
        snap = self.command("inventory", server=False)  # discovery, not just explicit socket
        document = self.plan(snap)
        second = copy.deepcopy(document["stops"][0])
        second.update(id="edge-case", title="Review an edge case")
        second["locations"][0]["line"] = 25
        document["stops"].append(second)
        self.install_plan(document)
        first = self.command("show", "behavior")
        self.assertEqual(first["location"]["line"], 3)
        self.assertEqual(first["stop"]["status"], "pending")
        self.assertEqual(self.command("context")["path"], "source.txt")
        self.assertEqual(self.command("preview")["preview"], "previous-source")
        second = self.command("next")
        self.assertEqual(second["stop"]["id"], "edge-case")
        self.assertEqual(second["location"]["line"], 25)
        done = self.command("next")
        self.assertTrue(done["ready_to_ask_about_staging"])
        self.assertIn("not every line", done["coverage"])
        self.assertEqual((self.root / ".git/index").read_bytes(), before)
        self.assertEqual(self.lua("return tostring(vim.wo.diff)"), "false")

    def test_user_must_have_seen_current_revision_and_skip_stays_unresolved(self):
        self.install_plan()
        self.assertIn("no current stop", self.command("next", success=False))
        self.command("show", "behavior")
        deferred = self.command("skip", "--reason", "Need product decision")
        self.assertFalse(deferred["ready_to_ask_about_staging"])
        self.assertEqual(deferred["stops"][0]["status"], "unresolved")
        self.command("show", "behavior")
        self.assertTrue(self.command("next")["ready_to_ask_about_staging"])

    def test_unsaved_edits_refuse_approval_and_preserve_the_buffer(self):
        self.install_plan()
        self.command("show", "behavior")
        self.lua("vim.api.nvim_buf_set_lines(0, 2, 3, false, {'manual unsaved edit'}); return true")
        self.assertIn("not been shown", self.command("next", success=False))
        self.assertIn("changed since planning", self.command("show", "behavior", success=False))
        context = self.command("context")
        self.assertTrue(context["modified"])
        self.assertIn("manual unsaved edit", context["lines"])
        self.assertNotIn("manual unsaved edit", (self.root / "source.txt").read_text())
        snap = self.command("inventory")
        self.command("plan", "--file", "-", stdin=json.dumps(self.plan(snap)))
        self.command("show", "behavior")
        done = self.command("next")
        self.assertEqual(done["unsaved"], ["source.txt"])
        self.assertFalse(done["ready_to_ask_about_staging"])

    def test_owner_pause_and_insert_mode_protect_user(self):
        self.install_plan()
        self.assertIn("does not own", self.command("show", "behavior", owner="agent-b", success=False))
        self.assertIn("different controller", self.command("resume", owner="agent-b", success=False))
        self.lua("vim.cmd('GuidedReviewPause'); return true")
        self.assertIn("paused", self.command("show", "behavior", success=False))
        self.lua("vim.cmd('GuidedReviewResume'); vim.api.nvim_input('i'); return true")
        self.assertIn("busy", self.command("show", "behavior", success=False))
        self.lua("vim.api.nvim_input(vim.keycode('<Esc>')); return true")
        self.command("show", "behavior")

    def test_release_resume_persistence_and_takeover(self):
        self.install_plan()
        self.command("show", "behavior")
        self.command("next")
        self.command("release")
        self.assertTrue(self.command("resume", owner="agent-b")["ready_to_ask_about_staging"])
        self.command("resume", "--take-over", owner="agent-c")
        self.assertIn("does not own", self.command("show", "behavior", owner="agent-b", success=False))
        self.assertEqual(self.command("status")["reviewed_count"], 1)

    def test_deleted_source_is_read_only_and_unusual_paths_are_literal(self):
        (self.root / "deleted.txt").unlink()
        unusual = "apostrophe ' 中文.txt"
        (self.root / unusual).write_text("literal path\n")
        snap = self.command("inventory")
        document = self.plan(snap, ["deleted.txt", unusual])
        self.install_plan(document)
        deleted = self.command("show", "behavior")
        self.assertTrue(deleted["location"]["historical"])
        self.assertEqual(self.lua("return tostring(vim.bo.modifiable)"), "false")
        self.command("show", "behavior")  # repeated history display must not collide on buffer name
        literal = self.command("show", "behavior", "--location", "2")
        self.assertEqual(literal["location"]["path"], unusual)
        self.assertEqual(literal["location"]["lines"], ["literal path"])

    def test_staged_only_preview_and_supporting_detour(self):
        self.git("add", "source.txt")
        self.install_plan()
        self.command("show", "behavior")
        self.command("preview")
        self.command("visit", "deleted.txt", "1")
        self.assertEqual(self.command("status")["current"], "behavior")
        self.command("show", "behavior")
        self.assertTrue(self.command("next")["ready_to_ask_about_staging"])

    def test_replans_preserve_reviewed_topic_and_stale_disk_changes_refuse_navigation(self):
        self.install_plan()
        self.command("show", "behavior")
        self.command("next")
        snap = self.command("inventory")
        self.command("plan", "--file", "-", stdin=json.dumps(self.plan(snap)))
        self.assertEqual(self.command("status")["reviewed_count"], 1)
        (self.root / "source.txt").write_text("replacement\n")
        self.assertIn("changed since planning", self.command("show", "behavior", success=False))
        self.assertEqual(self.command("status")["stops"][0]["status"], "changed")

    def test_existing_gitsigns_preview_and_original_keys_are_used(self):
        location = os.environ.get("GITSIGNS_NVIM_PATH")
        if not location:
            self.skipTest("Set GITSIGNS_NVIM_PATH to run the optional real-Gitsigns check")
        self.assertTrue(Path(location).is_dir(), "GITSIGNS_NVIM_PATH must point to a Gitsigns checkout")
        self.lua("vim.opt.rtp:append(vim.env.GITSIGNS_NVIM_PATH); "
                 "dofile(vim.env.NVIM_REVIEW_PLUGIN_DIR .. '/tests/fixtures/gitsigns.lua'); return true")
        self.install_plan()
        self.command("show", "behavior")
        ready = self.lua("return vim.wait(3000, function() "
                         "local h = require('gitsigns').get_hunks(); return h ~= nil and #h > 0 end, 10)")
        self.assertEqual(ready, "true")
        self.assertEqual(self.lua("return vim.fn.maparg('<leader>hp', 'n', false, true).desc"), "git [p]review hunk")
        self.assertEqual(self.lua("return vim.fn.maparg('<leader>hn', 'n', false, true).desc"), "git [N]ext hunk")
        self.assertEqual(self.command("preview")["preview"], "gitsigns")
        current = self.lua("return vim.api.nvim_get_current_win()")
        again = self.command("preview")
        self.assertTrue(again["existing"])
        self.assertEqual(self.lua("return vim.api.nvim_get_current_win()"), current)
        self.assertEqual(self.lua("return tostring(vim.wo.diff)"), "false")

    def test_external_disk_change_reloads_only_clean_buffers_on_replanned_show(self):
        self.install_plan()
        self.command("show", "behavior")
        self.lua("vim.bo.autoread = false; return true")
        (self.root / "source.txt").write_text("replanned source\n" * 5)
        snap = self.command("inventory")
        self.command("plan", "--file", "-", stdin=json.dumps(self.plan(snap)))
        result = self.command("show", "behavior")
        self.assertEqual(result["location"]["lines"], ["replanned source"] * 5)
        self.assertFalse(result["location"]["modified"])
        self.assertEqual(self.lua("return tostring(vim.bo.autoread)"), "false")

    def test_malformed_plan_and_nonexistent_lines_are_refused_cleanly(self):
        self.command("start")
        for document in ([], {}, {"stops": ["bad"]}, {"stops": [], "omitted": "bad"}):
            with self.subTest(document=document):
                self.command("plan", "--file", "-", stdin=json.dumps(document), success=False)
        snap = self.command("inventory")
        document = self.plan(snap)
        document["stops"][0]["locations"][0]["line"] = 500
        self.command("plan", "--file", "-", stdin=json.dumps(document))
        self.assertIn("line no longer exists", self.command("show", "behavior", success=False))
        self.assertEqual(self.command("status")["reviewed_count"], 0)

    def test_binary_source_refused_and_index_is_not_refreshed(self):
        (self.root / "binary.dat").write_bytes(b"\x00binary\x01")
        snap = self.command("inventory")
        before = (self.root / ".git/index").stat().st_mtime_ns
        self.install_plan(self.plan(snap, ["binary.dat"]))
        self.assertIn("Binary file", self.command("show", "behavior", success=False))
        self.assertIn("Binary file", self.command("visit", "binary.dat", "1", success=False))
        self.assertEqual((self.root / ".git/index").stat().st_mtime_ns, before)

    def test_ambiguous_editor_discovery_requires_explicit_choice(self):
        records = self.directory / "state/editors"
        record = json.loads(next(records.glob("*.json")).read_text())
        record["server"] = str(self.directory / "another.sock")
        (records / "another.json").write_text(json.dumps(record))
        self.assertIn("No unique editor", self.command("inventory", server=False, success=False))
        self.assertTrue(self.command("inventory")["files"])


if __name__ == "__main__":
    unittest.main()
