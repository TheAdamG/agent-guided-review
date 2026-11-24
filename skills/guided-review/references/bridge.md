# Bridge usage

All commands return JSON, with nonzero exit status and an error on refusal.
Global options come before the subcommand:

```sh
nvim-review --repo /path/to/repo --server /path/to/nvim.sock --owner unique-agent-id inventory
```

Keep these three options fixed during the conversation. The examples below omit
them for readability. `--owner` defaults to `NVIM_REVIEW_OWNER`, then
`HC_TERMINAL_ID`. There is no generic shared owner outside Herdr.

## Route format

After reading the inventory and source, substitute its actual `snapshot` token
and choose real current line numbers:

```sh
nvim-review plan --file - <<'JSON'
{
  "snapshot": "TOKEN-FROM-INVENTORY",
  "stops": [
    {
      "id": "validation",
      "title": "Reject invalid uploads before scheduling work",
      "why": "Changes which failures the user sees and when work starts",
      "locations": [
        {"path": "src/upload.ts", "line": 42, "end_line": 60},
        {"path": "tests/upload.test.ts", "line": 18}
      ]
    }
  ],
  "omitted": [
    {"path": "generated/schema.ts", "reason": "Generated types mirror the validation model; explain with that topic"}
  ]
}
JSON
```

Each changed file must occur in a stop or `omitted`, not both. Additional unchanged
supporting-source locations are allowed within the worktree. This classifies
files, not every hunk. An empty route is allowed for genuinely routine changes;
explain that judgment rather than fabricating review stops. Send a revised full
plan to update/reanchor the route: unchanged stop identities, locations and content
retain their review status; changed ones return to pending.

## Commands during review

```sh
nvim-review start
nvim-review show validation
nvim-review show validation --location 2
nvim-review preview
nvim-review context
nvim-review messages
nvim-review visit src/queue.ts 20
nvim-review show validation
nvim-review next
nvim-review skip --reason "Need to confirm the timeout policy"
nvim-review status
nvim-review summary
nvim-review release
nvim-review resume
```

`next` and `skip` require that the current stop was shown at the current revision.
After the last stop, they return a summary instead of another stop. Unresolved
stops can be revisited with `show ID`, discussed, and accepted with `next`.

Navigation results include a `messages` array when output was captured (`show`
always includes the array, possibly empty). Each entry has `path`, `phase`, and
the unfiltered `text`; failed commands also have `error`. Phases are `load`,
`reload`, and `navigate`. Output has no reliable severity classification: plugins
can print failures as ordinary text. Error responses include the captured text
alongside the error. `messages` reads the full in-memory log for this Neovim
session without navigating or requiring an active review. The user can read the
same log in a scrollable window using `:GuidedReviewMessages` and close it with
`:q`. These messages cover synchronous command output, not custom notification
UIs or asynchronous plugin messages, which remain under those plugins' control.

If a file or Git baseline changed, the bridge refuses stale navigation/approval.
Refresh inventory, read the updated source/context, then send an updated plan.
New or changed omitted files require fresh consideration too. Saving a previously
unsaved file may invalidate its topics even if the source text is unchanged:
recheck it rather than forcing the decision through.

The bridge never sends messages into another agent, stages files, or changes
Herdr focus. It targets a local existing editor only. Cross-machine transport and
arbitrary branch/base-commit comparisons are outside the current version.
