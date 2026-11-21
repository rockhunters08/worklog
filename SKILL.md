# Worklog — find past sessions and track work across them

```yaml
name: worklog
description: >
  Look up and record the user's Claude Code sessions and work threads in the
  local worklog. Use when the user asks "which session did I do X in", "find
  the chat about Y", wants the resume command for old work, starts a
  substantial piece of work, says they are continuing something from another
  session or account, asks "what am I working on" or "what's open", or asks to
  log progress, change a priority, or hand a thread to another account.
```

## The tool

The `wl` CLI in this directory, over an append-only JSONL log that the user's
web app (`serve.py`, http://127.0.0.1:8787 by default) reads live. Safe to
write while the app is open. Run it as `./wl` from this directory, or by its
full path from anywhere.

```bash
wl catalog <words>                      # every Claude session on this machine, searched by what was typed
wl catalog -t <tag> -w <person> --brief # by tag (own or auto), by colleague named
wl session <uuid|prefix|name>           # one session: summary, names, dates, PRs, resume command
wl label <key> "label" -t tags -n note  # annotate a catalog session
./enrich.py <key>                       # (re)summarise a session: what happened, outcome, tags, people

wl ls                                   # open threads, by priority
wl show w4                              # full detail incl. notes + session hops
wl add "title" -p P1 -s acct/topic -w Jack -t surf -r services
wl note w4 "what just happened"         # log progress
wl sess w4 acct/new-session-name        # thread moved to another account/session
wl status w4 blocked | done | waiting
wl resume w4                            # handoff block for another account
wl due w4 2026-09-25                    # date to check back on it ('clear' to drop)
wl due                                  # what needs checking today + resume commands
wl log w4                               # log lines with ids; `wl log w4 n0 "..."` edits
```

## Finding an old session

The catalog is every transcript under the configured projects directory,
searchable by what the user typed plus summaries, PRs and tickets. Run
`wl catalog <words>` and hand back the `claude --resume` line. A session whose
name is shared with others (`wl session` prints a NOTE) should be resumed by
uuid.

## Rules

- Session names are `account/topic` when the user works across accounts, else
  just a topic.
- When the user says they're picking up work started elsewhere, find the thread
  (`wl ls` / `wl show`) and record the new session with `wl sess` before working.
- Log a note at meaningful checkpoints: a decision, a blocker, a PR number, not
  every command. The notes are what the next session reads.
- A check-on date is orthogonal to status; use it whenever the user says "look
  at this again on/in ...". `wl due` with no args answers "anything I should be
  checking today?".
- Don't invent threads for one-off questions. A thread is work that will
  plausibly outlive this chat.
