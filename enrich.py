#!/usr/bin/env python3
"""Summarise catalog sessions with a headless `claude -p` call and store the
result as `smeta` fields (summary, outcome, auto_tags, people) in worklog.jsonl.

    ./enrich.py                 # every session that needs it, 6 at a time
    ./enrich.py --limit 20      # first 20 of those (newest first)
    ./enrich.py flcp-heal 0fa4  # just these (name, uuid, or prefix)
    ./enrich.py --force ...     # redo even if already summarised
    ./enrich.py --dry-run       # print the digest it would send, no call

A session is re-summarised when it has grown by a quarter since last time.
The user's own label/tags/note are never touched. Calls run with
--no-session-persistence so they leave no transcript behind to be catalogued.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import catalog
import store
from config import CFG

TAGS = list(CFG["enrich"]["tags"])
_SELF = ", ".join(sorted(CFG["user"]["names"]))

SYSTEM = """You write terse catalog entries for a software engineer's past Claude Code sessions.
You are given a digest of one session: what the user typed (in order, sampled), the final reply,
pull requests, repos and ticket ids. Produce:
- title: a label of 3 to 7 plain words, the way a good ticket is titled: the subject and the
  problem or goal (e.g. "SES cost cut via dedicated IP pools", "Steve Johnson missing account").
  Use the session name as a hint when it has one. No filler like "session", "investigation",
  "work on".
- summary: 2-3 sentences, past tense, specific. What was investigated or built, for whom, and the key
  finding or change. Name systems, repos and numbers from the digest. No preamble, no "the user".
- outcome: one sentence on how it ended: what shipped (PR numbers), what was decided, or what is still open.
- tags: 1 to 4 from this list only: %s
- people: first names of colleagues mentioned as people the user works with. Never list the user
  themselves%s. Not vendors, not article authors, not product names. Empty list if none.
Output JSON only.""" % (", ".join(TAGS), (" (known as %s)" % _SELF) if _SELF else "")

SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "outcome": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "people": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "outcome", "tags", "people"],
})

_print_lock = threading.Lock()
SCRATCH = os.path.join(os.environ.get("TMPDIR", "/tmp"), "worklog-enrich")
os.makedirs(SCRATCH, exist_ok=True)
MCP_NONE = os.path.join(SCRATCH, "no-mcp.json")
with open(MCP_NONE, "w") as _f:
    _f.write('{"mcpServers":{}}')


def sample(prompts, n_head=15, n_mid=10, n_tail=15):
    if len(prompts) <= n_head + n_mid + n_tail:
        return prompts
    mid = prompts[n_head:-n_tail]
    step = max(1, len(mid) // n_mid)
    return prompts[:n_head] + mid[::step][:n_mid] + prompts[-n_tail:]


def digest(e, m):
    out = []
    if m.get("label"):
        out.append("Label the user gave this session: %s" % m["label"])
    if e.get("titles"):
        out.append("Session name(s): %s" % " -> ".join(e["titles"]))
    if e.get("ai_title"):
        out.append("Auto title: %s" % e["ai_title"])
    out.append("Project dir: %s   Active: %s to %s over %d day(s), %d prompts" % (
        catalog.project_of(e), e.get("created", "")[:10], e.get("last", "")[:10],
        len(e.get("days", [])), e.get("n_prompts", 0)))
    if e.get("repos"):
        out.append("Repos edited: %s" % ", ".join(sorted(e["repos"], key=lambda k: -e["repos"][k])))
    if e.get("prs"):
        out.append("Pull requests: %s" % ", ".join(e["prs"][:12]))
    if e.get("issues"):
        out.append("Tickets mentioned: %s" % ", ".join(e["issues"][:12]))
    if e.get("summary"):
        out.append("Compaction summary written mid-session: %s" % e["summary"][:800])
    out.append("\nWhat the user typed (sampled, in order):")
    for i, p in enumerate(sample(e.get("prompts", []))):
        out.append("- %s" % p[:300].replace("\n", " "))
    if e.get("last_reply"):
        out.append("\nFinal reply from Claude:\n%s" % e["last_reply"][:900])
    return "\n".join(out)


def ask(text):
    """One headless call. Without the MCP connectors, settings and CLAUDE.md
    the system prompt is a few hundred tokens instead of 100K, so a summary
    costs about a cent."""
    cmd = ["claude", "-p", "--no-session-persistence", "--tools", "", "--no-chrome",
           "--strict-mcp-config", "--mcp-config", MCP_NONE, "--setting-sources", "project",
           "--model", CFG["enrich"]["model"], "--output-format", "json", "--json-schema", SCHEMA,
           "--system-prompt", SYSTEM, "--max-budget-usd", str(CFG["enrich"]["max_budget_usd"])]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    r = subprocess.run(cmd, input=text, capture_output=True, text=True, env=env,
                       timeout=180, cwd=SCRATCH)
    try:
        env_ = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    if env_.get("is_error") or r.returncode != 0:
        raise RuntimeError(str(env_.get("result") or r.stderr)[:300])
    body = env_.get("structured_output") or env_.get("result")
    if isinstance(body, str):
        body = json.loads(body)
    return body, env_.get("total_cost_usd", 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*", help="session names, uuids or prefixes; default: all that need it")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=CFG["enrich"]["workers"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--all", action="store_true", help="include tiny sessions")
    ap.add_argument("--unlabelled", action="store_true", help="only sessions with no label of yours")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cat = catalog.scan()
    meta = store.session_meta()
    if a.keys:
        todo = []
        for k in a.keys:
            hits = catalog.resolve(cat, k)
            if not hits:
                sys.exit("no session matching %r" % k)
            todo += hits
    else:
        todo = [e for e in cat.values()
                if a.force or catalog.needs_summary(e, meta.get(e["sid"], {}))
                or (a.all and not meta.get(e["sid"], {}).get("enriched"))]
    if a.unlabelled:
        todo = [e for e in todo if not meta.get(e["sid"], {}).get("label")]
    todo.sort(key=lambda e: e.get("last", ""), reverse=True)
    if a.limit:
        todo = todo[:a.limit]
    print("%d session(s) to summarise" % len(todo), file=sys.stderr)
    if a.dry_run:
        for e in todo:
            print("=" * 70, e["sid"], catalog.title_of(e))
            print(digest(e, meta.get(e["sid"], {})))
        return

    total = [0.0]

    def one(e):
        m = meta.get(e["sid"], {})
        body, cost = ask(digest(e, m))
        tags = [t for t in body.get("tags", []) if t in TAGS][:4]
        fields = {"auto_label": body.get("title", "").strip()[:80],
                  "summary": body.get("summary", "").strip(), "outcome": body.get("outcome", "").strip(),
                  "auto_tags": tags, "people": [p.strip() for p in body.get("people", []) if p.strip()][:8],
                  "enriched": e.get("n_prompts", 0)}
        store.append({"op": "smeta", "sid": e["sid"], "fields": fields})
        with _print_lock:
            total[0] += cost
            print("%s  %-28s $%.3f  %s" % (e["sid"][:8], (m.get("label") or catalog.title_of(e))[:28],
                                            cost, fields["summary"][:90]))
        return e["sid"]

    failed = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(one, e): e for e in todo}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as exc:
                failed += 1
                e = futs[f]
                with _print_lock:
                    print("FAILED %s %s: %s" % (e["sid"][:8], catalog.title_of(e), exc), file=sys.stderr)
    print("done: %d ok, %d failed, $%.2f" % (len(todo) - failed, failed, total[0]), file=sys.stderr)


if __name__ == "__main__":
    main()
