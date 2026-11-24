# worklog

A local, searchable catalog of every Claude Code session on your machine, with a
summary of what each one did, and a small tracker for work that moves between
sessions and accounts.

Everything is derived from the transcripts Claude Code already writes. There is
no list to maintain: a session appears when its transcript does, updates as it
grows, gets summarised on its own, and disappears when the transcript is
deleted. Everything stays on the machine. The only dependency is Python 3.11+;
there is no build step, no database, and no network access except the
optional summariser, which calls your own installed `claude` CLI.

```bash
git clone <this repo> worklog && cd worklog
cp worklog.example.toml worklog.toml   # optional; see Configuration
./worklog.sh                           # http://127.0.0.1:8787, opens a browser
```

## Contents

- [What it shows](#what-it-shows)
- [How the catalog is built](#how-the-catalog-is-built)
- [Summaries](#summaries)
- [Threads](#threads)
- [CLI](#cli)
- [Configuration](#configuration)
- [Data and privacy](#data-and-privacy)
- [HTTP API](#http-api)
- [Files](#files)
- [Troubleshooting](#troubleshooting)
- [Caveats](#caveats)
- [License](#license)

## What it shows

The page opens on the **sessions** catalog: every transcript under
`~/.claude/projects`, the same files the `claude --resume` picker reads, newest
activity first, grouped by month.

Each row carries:

- a name: your **label** if you gave one, else the summariser's **auto label**
  (3-7 plain words from the session name and what was discussed), else the
  session's name (what `/rename` set), else Claude's auto title, else the
  first prompt
- **what happened**: a 2-3 sentence summary, with **where it ended** in the
  expanded row
- the `claude --resume "<name>"` command, click to copy. It is prefixed with
  `cd <project> &&` when the session did not run from your code root, and the
  expanded row also offers the by-uuid form, which is the one to use when the
  red `name ×N` chip says the name is shared by more than one session (resume
  by name then picks one of them)
- tags (yours and auto), the colleagues named, how many PRs it opened, how many
  prompts it had, and any tracker thread that ran in it
- an **active** pill when the session had activity inside the last
  `catalog.active_hours` (24 by default); the masthead counts them and the
  `active now` chip filters to them. A session seen for the first time shows
  `new` until its summary lands, which in auto mode is within about half a
  minute of discovery

The expanded row adds the PRs as links, tickets mentioned, repos edited, the
first prompt, the closing reply, every name the session has had, the uuid, the
dates, and editors for your label, tags and note.

**Search** runs on the server over everything you typed in each session plus
labels, names, summaries, outcomes, tags, people, PR links, ticket ids, repos
and the closing reply. Every word must match. The expanded row shows the
matching snippet. The rail filters by tag, people, repo, project directory and
month, each chip with a count that respects the other filters. The view bar
sorts by last activity, start date, length or name, and switches between
everything, labelled and unlabelled. Tiny sessions (one prompt, nothing saved)
stay hidden unless `include tiny` is on.

Deep links: `#s/<uuid>` opens one session; `#threads` and `#w7` open the
tracker and one thread.

## How the catalog is built

`catalog.py` scans `<projects>/<project dir>/<uuid>.jsonl`. Each transcript is
read once in full and then only the bytes appended since the last pass, so the
first scan of a few hundred sessions takes seconds and a rescan takes
milliseconds. The web app rescans when a request arrives and the cache is more
than a minute old; the first scan over an empty cache runs in a background
thread so the page stays responsive. The cache is `sessions-cache.json` and can
be deleted at any time.

From each transcript it keeps:

| field | from |
|---|---|
| names | `custom-title` records (every name the session has had; the last is current), plus `custom-title.json` beside the subagent directory |
| auto title | the last `ai-title` record |
| prompts | every user message you typed, with harness-injected reminders stripped and tag-only messages (task notifications, `!` shell output, slash command echoes) dropped; the first 400 characters of each, up to 1,200 prompts, for search |
| first prompt, dates, days active, project directory, git branch | the user records |
| PRs | `pr-link` records |
| repos edited | `file_path` arguments in tool calls that fall under your code root |
| tickets | the ticket regexes in your config, run over the prompts |
| closing reply | the last assistant text message |
| compaction summary | the `summary` record Claude Code writes when it compacts |

Sub-agent transcripts (`isSidechain`) are ignored. A session whose transcript
is deleted leaves the catalog on the next scan; anything you attached to it
(label, tags, note, summary) stays in the event log keyed by its uuid, inert.

## Summaries

`enrich.py` sends a digest of a session to a headless `claude -p` call and
stores the result on the session: a short **auto label**, a 2-3 sentence **summary**, a one-line
**outcome**, 1-4 **tags** from the vocabulary in your config, and the
**people** named (your own names, from the config, are filtered out). The
digest is the names, dates, repos, PRs, tickets, the compaction summary if any,
up to 40 prompts sampled from the start, middle and end of the session, and the
closing reply.

The call runs with no tools, no MCP servers, no settings and no CLAUDE.md, so
its overhead is a few hundred tokens: about a cent and 25 seconds per session
with the default `haiku` model, 8 in parallel. A session is summarised again
when it has grown by a quarter since.

With `enrich.auto = true` in the config the web app runs this itself. A
session seen for the first time is summarised as soon as it is discovered (it
has to have more than one prompt). Sessions that have grown by a quarter since
their summary are redone in a run at most every ten minutes, so a session you
are typing into right now is not re-summarised on every poll. The view bar shows `summarising…` while it
runs and `summarise N new` with a button when it is off. The expanded row of a
stale session has a `summarise now` / `refresh` link.

By hand:

```bash
./enrich.py                  # every session with no summary, or grown by a quarter since
./enrich.py --limit 20       # newest 20 of those
./enrich.py flcp-heal 0fa4   # just these (name, uuid or prefix); --force to redo
./enrich.py --dry-run        # print the digests it would send, no call
```

Your own label, tags and note are never touched by it.

## Terminals

Every session row has a **term** chip, and the expanded row has **terminal
here** and **open in Terminal**. Both resume the session by uuid, in the
directory it ran from, under the account it last ran with (below).

- **terminal here** opens a terminal in a dock at the bottom of the page, one
  tab per session, so you can flip between your running sessions without
  leaving the catalog. Drag the dock's top edge to resize it, the arrows to
  fill the window. `#t/<uuid>` deep-links a session with its terminal open.
- **open in Terminal** opens the same session in your terminal application:
  macOS Terminal.app by default, iTerm with `launch.terminal = "iTerm"`, or any
  other with `launch.terminal = "command"` and a `launch.command` template that
  gets `{cmd}` and `{cwd}`, for example `open -na Ghostty --args -e {cmd}`.
- With **tmux** installed (`brew install tmux`) a session's terminal is a tmux
  session named `wl-<uuid prefix>`, and the page, your terminal app and a plain
  `tmux attach` all reach the same running `claude`. Closing a tab or window
  only detaches; opening the session again reattaches. Without tmux the page
  gets a bare pty and closing the tab ends the conversation. `launch.tmux =
  false` turns the wrapping off.
- `./wl launch <session>` does the same from a shell; `-p` prints the command
  instead.

The in-page terminal is xterm.js (vendored under `static/`) over a WebSocket to
a pty; the WebSocket server is a few dozen lines in `term.py`, so the app is
still standard-library only.

## Accounts

Claude Code keeps one login per config directory, so running sessions under
several accounts means one `CLAUDE_CONFIG_DIR` per account. Each transcript
records the login it ran under, and the catalog resumes a session with that
one; sessions from before that was recorded use `accounts.default`.

```bash
./wl account add you+work@example.com work   # ~/.claude-accounts/work, recorded in worklog.toml
CLAUDE_CONFIG_DIR=~/.claude-accounts/work claude auth login --email you+work@example.com
./wl account ls                              # every account and whether it is logged in
```

An account directory is a set of symlinks back into `~/.claude` (projects,
settings, skills, memory, and so on), so every account sees the same
transcripts and configuration; only the login differs. The expanded row shows
which account a session will resume under.

## Security of the spawn endpoints

The page is a browser tab, and other sites open in the same browser can send
requests to `127.0.0.1`. Anything that runs a command (`launch`, the terminal
WebSocket) therefore requires a token minted when the server starts and
injected into the page: another origin can request the page but cannot read
it, so it never sees the token. Requests carrying an `Origin` header from
anywhere but the server's own address are refused regardless. The server still
binds to 127.0.0.1 only.

## Threads

The **threads** switch under the masthead is a tracker for work that outlives
one chat: title, priority P0-P3, status, people, tags, a check-on date, and the
history of which session each leg ran in. If you work across several Claude
accounts, name sessions `account/topic` and the account shows as a chip.

- **open / everything / done & parked** tabs, each with a count
- drag a row to reorder it, or into another priority band
- **n** new thread, **/** search, **esc** close, click a row to expand
- **copy handoff** puts a markdown block on the clipboard with the title,
  priority, people, session history and every note, to paste as the first
  message of a chat elsewhere
- **check on** pins a thread to the top of the page on that date; overdue goes
  red
- log lines are editable in place; the underlying file is append-only so the
  original text is always still in it
- **start a thread** on a catalog session opens one with that session already
  attached; a thread chip on a session row jumps to it

## CLI

`wl` is the same store from a terminal, for scripts and for Claude sessions
that log their own progress (`SKILL.md` tells a Claude session how).

```bash
./wl catalog waf cloudfront          # sessions mentioning all of those words, with summaries
./wl catalog -t infra -w marty --brief
./wl catalog --unlabelled --project rock --json
./wl session flcp-heal               # names, dates, summary, PRs, resume command, file
./wl label a5909769 "flcp heal" -t flcp -n "PR #13079"

./wl add "Waves CarPlay sleep timer" -p P0 -s work/waves-carplay -w Jack -t waves
./wl ls                              # open threads by priority; --all, --due, --json
./wl show w2
./wl note w2 "repro'd on device, not sim"
./wl sess w2 work/carplay-cont2      # thread moved to another session
./wl due w2 2026-09-25               # check on it that day; `wl due` lists what is due
./wl resume w2                       # print the handoff block
./wl log w2                          # log lines with ids; `wl log w2 n0 "..."` edits, --rm deletes
./wl status w2 blocked | done | waiting
./wl md                              # regenerate WORKLOG.md
```

`wl session` and `wl label` take a uuid, a uuid prefix, or a session name
(current or former). `WL_SESSION=acct/topic` in the environment makes `add` and
`note` stamp the session automatically.

## Configuration

`worklog.toml` next to the code (or the file named by `WL_CONFIG`). Every key
is optional; `worklog.example.toml` lists them all with their defaults.

| key | what it does |
|---|---|
| `paths.projects` | where Claude Code writes transcripts (`~/.claude/projects`) |
| `paths.code_root` | sessions run from here resume without a `cd`; its subdirectories are the repo names |
| `paths.log` | the event log (`worklog.jsonl`), relative to the code directory |
| `server.port` | `8787` |
| `catalog.active_hours` | sessions with activity inside this window show as active (`24`) |
| `user.names` | your names and handles, so the summariser never lists you as a colleague |
| `github.org` | PRs in this org show as `repo#123`; others as `owner/repo#123` |
| `tickets.patterns` | regexes for ticket ids to extract (Jira keys and `repo#123` by default) |
| `enrich.auto` | let the web app summarise new and grown sessions by itself (`false`) |
| `enrich.model`, `workers`, `max_budget_usd` | the summariser's `claude` model, parallelism and per-call cap |
| `enrich.tags` | the tag vocabulary the summariser picks from |

Environment variables `WL_PORT`, `WL_LOG` and `WL_CONFIG` override the file.

## Data and privacy

Nothing leaves the machine except the summariser's digests, which go to
whatever your `claude` CLI is logged into. The server binds to `127.0.0.1`
only.

- `worklog.jsonl` is the only file worth backing up. It is an append-only log
  of events; every write from the web app, the CLI or a Claude session is one
  line, so concurrent writers never clobber each other, and edits and deletes
  are new events, never rewrites. Thread events carry `id` (`w7`); session
  metadata events carry `op: "smeta"` and `sid` (the uuid) with `label`,
  `tags`, `note` (yours) and `summary`, `outcome`, `auto_tags`, `people`,
  `enriched` (the summariser's).
- `sessions-cache.json` holds prompt text from every transcript. Delete it to
  rebuild; never commit it.
- `worklog.toml` holds your names and org. It is git-ignored.

## HTTP API

All on `127.0.0.1:<port>`, JSON in and out, no auth.

| method | path | what |
|---|---|---|
| GET | `/api/state` | every thread, plus the session names, people and tags seen |
| GET | `/api/sessions?q=<words>&rescan=1` | catalog rows matching the search, with scan and summarise status |
| GET | `/api/handoff?id=w7` | the handoff markdown for one thread |
| POST | `/api/event` | one event: `add`, `set`, `note`, `sess`, `note_edit`, `sess_edit`, `note_del`, `sess_del`, `del`, `reorder` for threads; `smeta` (label/tags/note on a `sid`) and `enrich` (`sid` optional) for sessions |

## Files

- `serve.py` web app; `index.html` the whole UI; `wl` the CLI; `worklog.sh` start/stop launcher
- `store.py` the event log reduced into threads and session metadata
- `catalog.py` transcript scanner, cache and search; `enrich.py` the summariser
- `config.py` defaults and `worklog.toml` loading
- `SKILL.md` instructions a Claude session can load to use `wl` itself

## Troubleshooting

- **A session is missing.** Its transcript is not under `paths.projects`, or it
  is tiny (toggle `include tiny`), or it was started by a tool that disables
  session persistence. Check `ls ~/.claude/projects/*/` for the uuid.
- **The page looks stale after an upgrade.** Every API response carries the
  mtime of `index.html` and the page reloads itself when it changes, unless
  you are mid-edit. If a tab predates that feature, reload it once by hand.
- **`summarise` does nothing or shows an error.** Run `./enrich.py --limit 1`
  in a terminal to see the real message. The usual causes: `claude` is not on
  the PATH of the shell that started the server, or it is not logged in
  (`claude -p 'hi'` should answer).
- **Summaries are expensive.** The `claude` call must run with no MCP servers
  and no settings (the flags in `enrich.py`); with them it loads 100K tokens of
  tool definitions per call. The `done:` line in `enrich.log` prints the total.
- **Two sessions share a name.** Resume by uuid; the expanded row has the
  command.

## Caveats

- The scanner reads Claude Code's private transcript format: `custom-title`,
  `ai-title`, `pr-link` and `summary` records and the user/assistant messages.
  That is not a public contract and may change between releases; the record
  names are all in `catalog.py` in one place.
- `enrich.py` needs a logged-in `claude` CLI with `--no-session-persistence`,
  `--tools`, `--strict-mcp-config`, `--mcp-config`, `--setting-sources`,
  `--json-schema` and `--system-prompt`. Without `--no-session-persistence`
  each call would itself become a session in the catalog. `--bare` is
  deliberately not used: it skips the keychain and the call runs logged out.

## License

MIT, see `LICENSE`.
