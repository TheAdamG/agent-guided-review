#!/usr/bin/env python3
"""Install checkout-backed CLI/skill links; never replace unrelated user files."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def owned_link(destination, source):
    try:
        return destination.is_symlink() and destination.resolve() == source.resolve()
    except (OSError, RuntimeError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin")
    parser.add_argument("--skills", choices=("opencode", "claude"), nargs="+", default=[],
                        help="Also install the shared skill for these harnesses")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without changing files")
    parser.add_argument("--uninstall", action="store_true", help="Remove only links owned by this checkout; preserve review state")
    args = parser.parse_args(argv)
    if os.name != "posix":
        parser.error("The bridge currently supports POSIX systems, not native Windows")
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    skill = ROOT / "skills/guided-review"
    links = [(args.bin_dir.expanduser().absolute() / "nvim-review", ROOT / "bin/nvim-review")]
    destinations = {
        "opencode": config / "opencode/skills/guided-review",
        "claude": Path.home() / ".claude/skills/guided-review",
    }
    links.extend((destinations[name], skill) for name in dict.fromkeys(args.skills))
    try:
        # Preflight every path before creating or removing any link.
        for destination, source in links:
            if not source.exists():
                raise ValueError(f"Incomplete checkout: missing {source}")
            if os.path.lexists(destination) and not owned_link(destination, source):
                raise ValueError(f"Refusing to replace/remove existing path: {destination}. Inspect it and move it aside yourself if appropriate.")
        for destination, source in links:
            exists = os.path.lexists(destination)
            if args.uninstall:
                if exists:
                    print(f"{'Would remove' if args.dry_run else 'Removing'} {destination}")
                    if not args.dry_run:
                        destination.unlink()
            elif not exists:
                print(f"{'Would link' if args.dry_run else 'Linking'} {destination} -> {source}")
                if not args.dry_run:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.symlink_to(source, target_is_directory=source.is_dir())
            else:
                print(f"Already installed: {destination}")
        if not args.uninstall:
            print(f"Keep this checkout in place and make sure {args.bin_dir.expanduser()} is on PATH.")
        return 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
