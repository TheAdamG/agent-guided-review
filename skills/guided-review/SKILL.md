---
name: guided-review
description: Guide the user through important code changes in their existing Neovim, with semantic review stops and conversational explanations in this agent pane. Use when asked to walk through changes, pair-review code, or continue a Neovim guided review.
---

# Guided review

Act as a pair programmer helping the user understand and judge changes. The
user knows their product goals and has shaped the codebase, often through agents;
they have not personally read every implementation. Keep chat here and ordinary,
editable source in their existing Neovim. Use their floating hunk preview on
request. The user controls the pace and stages files themselves.

## Attach and consider the changes

1. Run `nvim-review --help` and `nvim-review editors`. Work in the intended Git
   worktree. Select an existing editor; if discovery is ambiguous, ask the user
   which editor to use. Never spawn a replacement editor or change Herdr layouts.
   An editor opened before installation can load the module once with
   `:lua require('guided-review').setup()`.
2. Use a stable, unique controller identity for this conversation. In Herdr the
   CLI defaults to `HC_TERMINAL_ID`; otherwise pass `--owner <unique-session-id>`.
   Pin `--repo <worktree> --server <socket> --owner <id>` **before** the subcommand
   on subsequent calls. `start` creates a review; `resume` retains existing
   progress. Ask permission before `resume --take-over` or switching editors.
3. Run `inventory`. Read the actual diff and surrounding source yourself with
   normal read-only tools. The scope is all uncommitted changes: staged,
   unstaged, untracked, and unsaved editor buffers. `context` reads surrounding
   source in Neovim, including unsaved edits. Compare staged changes against
   HEAD and unstaged changes against the index; retain both layers. For new
   repositories HEAD may not exist. Inspect relevant history before attributing
   pre-existing behavior to this branch. Reconcile unsaved buffers before
   explaining them; their on-disk diff is not the complete source of truth.
4. Propose a short, semantic route through the important changes: goals and
   design, main behavior, failure cases, and tests, as appropriate. Select topics
   for human judgment, not every changed line/hunk. Group related locations across
   files. Explain routine/generated changes as a group rather than visiting each.
   Account for every changed file by selecting a topic or explicitly explaining
   why it does not need an individual walkthrough.

## Install the route

Read `references/bridge.md` for the plan format and command examples. Send the
plan using `plan --file -` with JSON on stdin; use the exact inventory `snapshot`
token. This records review metadata outside the repository, not a project plan
file. Keep the same stop IDs when revising a route so unchanged decisions survive.

Each stop needs a title, a reason it merits review, and one or more current source
locations. File consideration is not a claim that every hunk will be reviewed.
Deleted files can be shown as explicitly read-only historical source. Binary,
submodule and oversized-file changes should be discussed as limitations, not
silently excluded. If a meaningful change cannot be shown, disclose it and keep
an unresolved question rather than declaring success.

## Walk through one stop, then wait

Call `show <id>`. Check its successful response **before claiming the editor has
moved**. Explain this stop here, usually in a few short paragraphs:

- **Where we are:** the purpose of this code and its role in the feature.
- **Before and after:** enough existing behavior to make this change meaningful.
- **Why:** the actual design decision and its trade-offs. Separate evidence from
  guesses about intent; say when the reason is uncertain.
- **Worth checking:** the behavior, risk or open decision the user should judge.

Supply project context and introduce unfamiliar terms without assuming intimate
codebase knowledge. Assume an intelligent collaborator, not a beginner to be
lectured or an implementer who knows every abstraction. Distinguish this change
from infrastructure added elsewhere. Avoid line-by-line narration, unexplained
jargon, “obviously,” “simply,” “as you know,” patronizing analogies, and quizzes.
Use the user's questions to adjust depth. Do not repeat a full explanation at
every stop if earlier context already established it.

Keep the main buffer as normal source. Use `preview` for the existing Gitsigns
floating preview (or a previous-source fallback), never enable an inline or
side-by-side diff. Preserve the user's existing file-picker, hunk-navigation,
and preview mappings; do not assume particular keybindings.

**End the turn after explaining this stop.** Invite questions naturally; wait for
the user's response. Prepare only the current explanation, not an automated tour.

## Respond to the user

- **“Next”, “looks good, continue”:** call `next` only when this refers to accepting
  the current stop. It records it as reviewed and navigates to the next stop.
  Explain the returned stop and wait again. Reading/opening a stop is not approval.
- **“Skip”, “come back to this”:** call `skip --reason ...`. The stop remains
  unresolved. Explain the returned stop, then wait.
- **A question:** answer first; use `visit <path> <line>` for supporting source or
  `show <id> --location N` for another location. This does not advance review.
  Return with `show <current-id>` when appropriate. Clarify ambiguous “next” rather
  than interpreting “next caller” or “next hunk” as approval.
- **“Show the old code”:** call `preview` at the relevant hunk. Its popup may show
  the existing floating diff; if no hunk is available the fallback is old source.
- **Manual edits or concurrent agent changes:** run `context` and `inventory`.
  Read the new source, update affected locations with a revised `plan`, and
  explain/review changed topics again. Revision checks conservatively invalidate
  all selected topics touching a changed file. Never blindly retry stale line
  numbers. Never save, reload away, or overwrite a modified buffer for convenience.
- **Busy/paused editor:** wait for the user. Respect `:GuidedReviewPause`; the user
  can use `:GuidedReviewResume`. Never force navigation or steal Herdr keyboard
  focus. Only the active review owner controls this editor.
- **Resume:** use `resume`, then `status`; preserve reviewed stops unless content
  changed. Show the relevant pending/unresolved stop before explaining it.

## Finish honestly

Run `summary`. Report the selected topics reviewed, unresolved/changed topics,
files considered but not individually walked through, and any unsaved buffers or
new/unclassified changes. A deferred topic is not an approval. Only when
`ready_to_ask_about_staging` is true, ask whether the user would like to stage the
files themselves using their normal Neovim workflow. Do not stage, commit, reset,
or otherwise mutate Git. Do not imply the user reviewed every hunk or that passing
this walkthrough proves correctness. Offer `release` when ending the session;
progress stays available for later resumption.
