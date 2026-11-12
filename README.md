# nvim_review

**Let your coding agent guide you through changes in your existing Neovim.**

The agent chooses a meaningful order, opens the relevant source, explains what
changed and why, and waits for your questions. Chat stays in Claude Code or
OpenCode. Your editor stays an ordinary, editable source view.

This is for people who want help understanding a large change set without reading
everything as an inline or side-by-side diff. Keep using your existing changed-file
picker, hunk navigation and floating previews. No keybindings are replaced.

## How a review feels

Ask your agent:

> Use guided-review to walk me through the important changes in Neovim.

It considers the changes and selects review topics, rather than mechanically
visiting every hunk. For example:

1. The new configuration model and the behavior it makes possible.
2. How requests now use that configuration.
3. Failure cases and compatibility decisions.
4. The tests that cover those decisions.

At each stop it navigates your editor, supplies the necessary codebase context,
and explains the before/after behavior and trade-offs. It assumes an intelligent
collaborator who knows the feature goals, not someone who wrote every line. Ask
questions, follow a caller, request a floating preview, or edit the source yourself.

- **“Next”** accepts the current topic and continues.
- **“Skip”** leaves it unresolved and continues.
- **Questions and detours** do not advance the review.
- **Changes after review** invalidate affected topics for another look.

At the end, the agent reports what was reviewed, what was not individually walked
through, and what remains unresolved. When ready, it asks whether you want to
stage the files **yourself**. The bridge has no staging or commit operation.

## What runs where

| Part | Role |
| --- | --- |
| Neovim Lua module | Navigate an existing editor, show previews, expose current buffer context, and protect active editing. |
| `nvim-review` Python command | Read Git changes, talk to Neovim's local socket, and persist review decisions. |
| Shared agent skill | Select an explanatory route and run the conversational review in Claude Code or OpenCode. |

Herdr is **optional**. Inside Herdr, terminal identities and tab labels help select
the right editor and keep controllers separate. Outside Herdr, give the agent a
unique `--owner` identity. No model SDK, MCP server, paid API, or additional chat UI
is needed; the agent uses its normal shell tool to run the bridge.

## Requirements

- **Neovim 0.12+** (tested with 0.12.6).
- **Python 3.10+** and Git, with `nvim`, `python3`, and `git` on PATH.
- A local POSIX environment. Tested on Linux; macOS is not yet verified. Native
  Windows and cross-machine editor control are not supported.
- Claude Code or OpenCode with skill discovery and permission to run the bridge.
- Optional: [gitsigns.nvim](https://github.com/lewis6991/gitsigns.nvim), configured
  normally, for its floating hunk preview. Without it, the bridge shows a bounded
  popup of previous source instead.

The Python implementation uses only the standard library; no pip installation
or Neovim Python provider is needed.

## Installation

There are **three parts to install**: the Lua plugin, the command on PATH, and the
agent skill. Installing the Lazy plugin alone does not install the other two.

### Option A: one local checkout, managed with Lazy

Before this project is published, use your existing checkout. Once published,
replace `YOUR_GITHUB_USERNAME` below with the actual GitHub owner:

```sh
git clone https://github.com/YOUR_GITHUB_USERNAME/nvim_review.git ~/dev/nvim_review
```

Add this to your [lazy.nvim](https://github.com/folke/lazy.nvim) specifications:

```lua
{
  dir = vim.fn.expand("~/dev/nvim_review"),
  name = "nvim-review",
  main = "guided-review",
  lazy = false,
  opts = {
    quiet_ms = 800,
  },
}
```

`main` is explicit because the repository name and Lua module name differ. Run
`:Lazy sync`, then install the command and skills from that same checkout:

```sh
cd ~/dev/nvim_review
python3 scripts/install.py --skills opencode claude
```

The installer creates these symlinks:

```text
~/.local/bin/nvim-review
  -> <checkout>/bin/nvim-review

${XDG_CONFIG_HOME:-~/.config}/opencode/skills/guided-review
  -> <checkout>/skills/guided-review

~/.claude/skills/guided-review
  -> <checkout>/skills/guided-review
```

Keep the checkout in place and ensure `~/.local/bin` is on PATH. You may select
only one harness, change the bin directory, or preview the changes:

```sh
python3 scripts/install.py --skills claude
python3 scripts/install.py --bin-dir "$HOME/bin" --skills opencode
python3 scripts/install.py --dry-run --skills opencode claude
```

Running without `--skills` installs only the command. The installer is idempotent
for its own links and **refuses to overwrite any existing file, directory, or
unrelated symlink**. It does not download dependencies or edit agent settings.

### Option B: let Lazy clone the published repository

Once the remote exists, use this instead of Option A's `dir` specification:

```lua
{
  "YOUR_GITHUB_USERNAME/nvim_review",
  name = "nvim-review",
  main = "guided-review",
  lazy = false,
  opts = { quiet_ms = 800 },
}
```

After `:Lazy sync`, run the installer from Lazy's checkout. With the default
Neovim app name and Lazy root:

```sh
python3 "${XDG_DATA_HOME:-$HOME/.local/share}/nvim/lazy/nvim-review/scripts/install.py" \
  --skills opencode claude
```

If you customize `NVIM_APPNAME` or Lazy's root, use the actual plugin directory
shown by `:Lazy` instead. Use one installation source for the plugin, command and
skill to avoid version mismatches.

### Other plugin managers or manual runtime loading

Add the checkout to Neovim's runtimepath and run setup:

```lua
vim.opt.rtp:prepend(vim.fn.expand("~/dev/nvim_review"))
require("guided-review").setup({ quiet_ms = 800 })
```

Install the command and skill with the same installer. To use the skill elsewhere,
copy or symlink the **whole** `skills/guided-review` directory, including its
`references` folder, into the harness's skill directory. It is one shared skill,
not two harness-specific implementations.

## First review

1. Open Neovim in the Git worktree you want to review. After installing the plugin
   in an already-running editor, load it once:

   ```vim
   :lua require('guided-review').setup()
   ```

2. Check the bridge from a shell:

   ```sh
   nvim-review --help
   nvim-review editors
   ```

3. Ask your agent to use **guided-review**. Claude Code also exposes
   `/guided-review`; OpenCode supports `@guided-review`. If the harness has not
   discovered the new skill, ask it to read `<checkout>/skills/guided-review/SKILL.md`
   directly or refresh skills using that harness's normal mechanism.

4. Discuss the current topic, edit normally, and say “next” when satisfied.

You should not need to manage plans or run bridge commands during a normal
review: the skill tells the agent how. The bridge interface and route format are
documented in [the skill reference](skills/guided-review/references/bridge.md).

### Your Neovim controls

| Command | Effect |
| --- | --- |
| `:GuidedReviewPause` | Block agent navigation while you work. |
| `:GuidedReviewResume` | Allow navigation again. |
| `:GuidedReviewInfo` | Show the attached controller and last published progress. |

The plugin adds no mappings, changes no terminal layout, and never takes Herdr
keyboard focus. It refuses navigation in editing/prompt modes and briefly after
recent cursor activity. Existing Gitsigns and file-picker mappings remain yours.

## Scope, safety and limitations

- Reviews cover **uncommitted** staged/unstaged/new files and unsaved buffers in
  the selected editor. Committed branch-wide comparisons are not implemented.
- A reviewed topic is not a claim that every changed line or hunk was inspected.
  Every changed file must be selected for a topic or have an explicit omission
  reason. The agent's explanations and topic choices are model behavior, not a
  deterministic correctness check.
- File-level fingerprints invalidate affected topics conservatively when source,
  the index, or Git HEAD changes. Concurrent changes detected during inventory
  cause a refusal rather than a silently inconsistent approval. The agent must
  refresh and reanchor its route. Saving an unsaved edit may require a recheck.
- Modified buffers are never automatically saved or discarded. Unmodified buffers
  may reload external changes when navigating. Normal Neovim autocmds still run.
- One controller/editor owns a worktree review at a time. Moving ownership requires
  explicit `resume --take-over`; this prevents accidental interference, not attacks
  by trusted code with access to the same local RPC socket.
- Deleted source can be viewed read-only from the index/HEAD. Source navigation is
  limited to 1 MiB files. Binary files, submodules and outside-worktree symlinks
  need explicit discussion rather than pretending they are ordinary source.
- The command reads repository content, Git metadata and selected Neovim buffers.
  It disables external Git diff/textconv drivers and optional index locks. There
  are no source-write, stage, reset or commit commands in the bridge.
- There are no network calls in the runtime. Your agent and your other Neovim
  plugins retain their own permissions and behavior.

Discovery records and review progress live outside the repository, under
`${XDG_STATE_HOME:-~/.local/state}/nvim-guided-review`. Set `NVIM_REVIEW_STATE_DIR`
to the same alternative directory in both Neovim and the agent environment if
needed. Progress survives restarts; resumption rechecks the current content.

## Troubleshooting

- **No editor found:** ensure setup ran, open a buffer or set Neovim's working
  directory inside the intended worktree, then run `nvim-review editors` again.
- **More than one editor:** choose one explicitly with `--server SOCKET`.
- **Outside Herdr:** the agent must pass a stable, unique `--owner` or set
  `NVIM_REVIEW_OWNER`; there is no shared default owner.
- **Busy or paused:** return to normal mode or use `:GuidedReviewResume`. The
  agent should wait, not force its way past an active editing session.
- **Stale stop:** the agent refreshes inventory, reads the changed source and
  revises the route. Repeatedly jumping to old line numbers is not the fix.
- **Installer collision:** inspect the existing path before moving anything aside.
  In particular, an earlier in-config installation is not automatically migrated
  or overwritten by this standalone package.

## Development and tests

From this checkout, with Git and Neovim on PATH:

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

Tests use disposable worktrees, state directories and headless `nvim -u NONE`.
Installer tests use a disposable HOME. They do not load your personal Neovim
configuration, install packages or stage anything in a real project.

One integration check needs an existing Gitsigns checkout; it is skipped unless
you provide one. Nothing is downloaded automatically:

```sh
GITSIGNS_NVIM_PATH=/path/to/gitsigns.nvim \
  python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

Set `NVIM_REVIEW_TEST_TMPDIR` to an existing writable directory to select the test
temporary location, if needed. Optional lint/format checks:

```sh
ruff check --no-cache bin/nvim-review scripts tests
stylua --check lua tests/fixtures
git diff --check
```

Source layout:

```text
bin/nvim-review                 Relocatable command entry point
lua/guided-review.lua            Neovim control and discovery
scripts/nvim_review/             Git inventory, review state, RPC client, CLI
scripts/install.py              Non-overwriting CLI/skill installer
skills/guided-review/            Shared skill and bridge reference
tests/                          Unit, headless-editor and installation checks
```

## Updating and uninstalling

Update the chosen checkout using Git or Lazy, then reload/restart Neovim when
convenient. Because the command and skills are linked, they follow that checkout.

From the **same checkout used to install**, remove its links with:

```sh
python3 scripts/install.py --uninstall --skills opencode claude
```

Pass the same `--bin-dir` if you customized it. The uninstaller removes only its
own symlinks, leaves replaced/user-owned paths alone by refusing the operation,
and preserves saved review progress. Remove the plugin specification separately.

## Publishing checklist

- Replace `YOUR_GITHUB_USERNAME` in the installation examples after choosing the
  repository owner.
- Choose and add a license before inviting third-party reuse. **No license has
  been selected in this initial package.**
- The live conversational experience still needs user testing; automated checks
  cover the bridge and editor behavior, not the quality of model explanations.
