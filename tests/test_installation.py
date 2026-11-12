"""Installation tests use a disposable HOME, never the user's real install."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="nvim-review-install-", dir=os.environ.get("NVIM_REVIEW_TEST_TMPDIR")
        )
        self.home = Path(self.temporary.name)
        self.config = self.home / "custom-config"
        self.environment = {**os.environ, "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.config)}
        self.binary = self.home / ".local/bin/nvim-review"
        self.opencode = self.config / "opencode/skills/guided-review"
        self.claude = self.home / ".claude/skills/guided-review"

    def tearDown(self):
        self.temporary.cleanup()

    def install(self, *args, success=True):
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/install.py"), *args],
            capture_output=True, text=True, env=self.environment, check=False, timeout=10,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_dry_run_does_not_create_directories(self):
        result = self.install("--dry-run", "--skills", "opencode", "claude")
        self.assertIn("Would link", result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_installed_launcher_and_skills_resolve_to_checkout(self):
        self.install("--skills", "opencode", "claude")
        self.assertEqual(self.binary.resolve(), ROOT / "bin/nvim-review")
        for link in (self.opencode, self.claude):
            self.assertEqual(link.resolve(), ROOT / "skills/guided-review")
            self.assertTrue((link / "references/bridge.md").is_file())
        result = subprocess.run([str(self.binary), "--help"], env=self.environment,
                                capture_output=True, text=True, check=True, timeout=10)
        self.assertIn("Agent-guided review", result.stdout)

    def test_reinstall_is_idempotent(self):
        self.install("--skills", "opencode", "claude")
        before = self.binary.lstat().st_mtime_ns
        self.assertIn("Already installed", self.install("--skills", "opencode", "claude").stdout)
        self.assertEqual(self.binary.lstat().st_mtime_ns, before)

    def test_existing_launcher_blocks_all_changes(self):
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text("keep this launcher")
        result = self.install("--skills", "opencode", "claude", success=False)
        self.assertIn("Refusing", result.stderr)
        self.assertEqual(self.binary.read_text(), "keep this launcher")
        self.assertFalse(self.config.exists())
        self.assertFalse(self.claude.exists())

    def test_existing_skill_blocks_cli_install_before_any_changes(self):
        self.opencode.mkdir(parents=True)
        marker = self.opencode / "SKILL.md"
        marker.write_text("keep this skill")
        self.install("--skills", "opencode", "claude", success=False)
        self.assertFalse(os.path.lexists(self.binary))
        self.assertEqual(marker.read_text(), "keep this skill")

    def test_dangling_links_are_not_overwritten(self):
        self.binary.parent.mkdir(parents=True)
        self.binary.symlink_to(self.home / "missing")
        self.install(success=False)
        self.assertEqual(self.binary.readlink(), self.home / "missing")

    def test_custom_bin_dir(self):
        directory = self.home / "my commands"
        self.install("--bin-dir", str(directory))
        self.assertEqual((directory / "nvim-review").resolve(), ROOT / "bin/nvim-review")
        self.assertFalse(os.path.lexists(self.binary))

    def test_uninstall_only_removes_its_links(self):
        self.install("--skills", "opencode", "claude")
        self.install("--uninstall", "--skills", "opencode", "claude")
        for link in (self.binary, self.opencode, self.claude):
            self.assertFalse(os.path.lexists(link))
        self.assertTrue((ROOT / "bin/nvim-review").is_file())
        self.assertTrue((ROOT / "skills/guided-review/SKILL.md").is_file())

    def test_uninstall_refuses_replaced_files_before_removing_other_links(self):
        self.install("--skills", "opencode", "claude")
        self.binary.unlink()
        self.binary.write_text("new launcher belonging to the user")
        self.install("--uninstall", "--skills", "opencode", "claude", success=False)
        self.assertEqual(self.binary.read_text(), "new launcher belonging to the user")
        self.assertTrue(self.opencode.is_symlink())
        self.assertTrue(self.claude.is_symlink())


if __name__ == "__main__":
    unittest.main()
