# agent-guided-review

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
  Other harnesses may work but are not officially supported; see
  [other agent harnesses](#other-agent-harnesses).
- Optional: [gitsigns.nvim](https://github.com/lewis6991/gitsigns.nvim), configured
  normally, for its floating hunk preview. Without it, the bridge shows a bounded
  popup of previous source instead.

The Python implementation uses only the standard library; no pip installation
or Neovim Python provider is needed.

## Installation

### Recommended: lazy.nvim

Add this to your [lazy.nvim](https://github.com/folke/lazy.nvim) plugin specs:

```lua
{
  "TheAdamG/agent-guided-review",
  name = "nvim-review",
  main = "guided-review",
  lazy = false,
  opts = {},
  build = "python3 scripts/install.py --skills opencode claude",
}
```

Run **`:Lazy sync`**. Lazy downloads the plugin, runs setup, and uses the build
step to install the `nvim-review` command and the skill for both agents. No manual
clone or separate installer command is needed. If you only use one agent, remove
`opencode` or `claude` from the build command.

Ensure **`~/.local/bin` is on PATH** in the shell where your agent runs. Then
continue to [your first review](#first-review).

The command and skills are symlinked to Lazy's checkout, so they stay together
when updated. The installer refuses to overwrite unrelated files or existing
installations from another checkout. `main` is explicit because the repository
and Lua module names differ. If the plugin was already installed before adding
`build`, run `:Lazy build nvim-review` once.

### Other agent harnesses

**OpenCode and Claude Code are officially supported.** The Neovim plugin and
Python bridge are harness-independent, and both agents use the same portable
skill. Other harnesses may work, but have not been verified end to end. They need
skill loading, shell-command access, and permission to reach the local Neovim
socket.

For harnesses supported by the [Skills CLI](https://skills.sh/), install just the
skill with:

```sh
npx skills add TheAdamG/agent-guided-review --skill guided-review -g
```

This requires Node.js 22.20+ and lets you choose the target agent. It installs
**only the skill**, not the Neovim plugin or `nvim-review` command. Install those
using the Lazy spec above with `build = "python3 scripts/install.py"` instead,
or use the manual instructions below. Do not install the same skill with both
installers. Separately installed skills must be kept in sync with the plugin and
bridge.

Alternatively, copy or symlink the **whole** `skills/guided-review` directory,
including `references`, into your harness's skill directory. Compatibility is
not an official support guarantee.

### Advanced: local checkout or other plugin managers

<details>
<summary>Manual installation and installer options</summary>

Clone the repository and install the command and skills:

```sh
git clone https://github.com/TheAdamG/agent-guided-review.git ~/dev/agent-guided-review
cd ~/dev/agent-guided-review
python3 scripts/install.py --skills opencode claude
```

For Lazy, use `dir = vim.fn.expand("~/dev/agent-guided-review")` in place of
`"TheAdamG/agent-guided-review"` in the recommended spec. For other plugin managers,
add that checkout as a local plugin and call `require("guided-review").setup()`.
Without a plugin manager, load it directly:

```lua
vim.opt.rtp:prepend(vim.fn.expand("~/dev/agent-guided-review"))
require("guided-review").setup()
```

Use the same checkout for the plugin, command and skill. The installer creates
these symlinks:

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

</details>

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
| `:GuidedReviewMessages` | Read captured navigation messages in a scrollable, read-only window; close with `:q`. |

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
- **“Press ENTER or type command to continue”:** Neovim is waiting for a message
  acknowledgement, not frozen. Press Enter yourself; use `:messages` afterwards
  to investigate the preceding output. Multi-line plugin/autocommand messages
  can cause this, especially with `cmdheight=0`. Review navigation captures
  synchronous file-opening/reload messages, returns them to the agent, and keeps
  them available through `:GuidedReviewMessages` (or `nvim-review messages`) until
  Neovim exits. This includes output printed before an error; thrown errors still
  fail the request. The agent must report warnings/errors rather than treating
  successful navigation as proof that plugin work succeeded. Navigation also
  checks only the target buffer for external changes. It
  never automatically dismisses prompts; asynchronous plugin messages or truly
  interactive prompts may still need your attention.
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

Tests use disposable worktrees, state directories and `nvim -u NONE`, both
headless and in a pseudo-terminal to catch blocking message prompts.
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
convenient. Command and skill links created by `scripts/install.py` follow that
checkout. If you installed the skill with the Skills CLI instead, update it
separately with `npx skills update guided-review -g`.

Before removing the plugin, open a shell in the **same checkout used to install**
(shown by `:Lazy` for Lazy installations) and remove its links with:

```sh
python3 scripts/install.py --uninstall --skills opencode claude
```

Use the same `--skills` selection as at installation (omit it for a command-only
install), and pass the same `--bin-dir` if you customized it. The uninstaller
removes only its own symlinks, leaves replaced/user-owned paths alone by refusing
the operation, and preserves saved review progress. Remove the plugin
specification separately.
Skills installed with the Skills CLI must instead be removed using
`npx skills remove guided-review -g`.

## Publishing checklist

- Choose and add a license before inviting third-party reuse. **No license has
  been selected in this initial package.**
- The live conversational experience still needs user testing; automated checks
  cover the bridge and editor behavior, not the quality of model explanations.
