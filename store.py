"""Shared storage for the worklog: append-only JSONL reduced into items."""
import datetime
import json
import os

from config import CFG

HERE = os.path.dirname(os.path.realpath(__file__))
LOG = CFG["paths"]["log"]
MD = os.path.join(HERE, "WORKLOG.md")

PRIOS = ["P0", "P1", "P2", "P3"]
STATUSES = ["active", "blocked", "waiting", "done", "parked"]
OPEN = ["active", "blocked", "waiting"]
# label/tags/note are the user's own; the rest come from enrich.py and are overwritten on re-run
SMETA_FIELDS = ("label", "tags", "note", "auto_label", "summary", "outcome", "auto_tags", "people", "enriched")


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def short(ts):
    return (ts or "")[:16].replace("T", " ")


def csv(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def append(ev):
    """Append one event. O_APPEND on a single small write is atomic enough for
    the handful of concurrent writers (browser tab + Claude sessions) we have."""
    ev["ts"] = now()
    with open(LOG, "a") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return ev


def load():
    items = {}
    if not os.path.exists(LOG):
        return items
    with open(LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            i, op, ts = ev.get("id"), ev.get("op"), ev.get("ts")
            if not i:
                continue
            if op == "add":
                items[i] = {
                    "id": i, "title": ev.get("title", ""), "prio": ev.get("prio", "P2"),
                    "status": "active", "people": [], "tags": [], "repo": "",
                    "due": "", "resume": "", "rank": None, "sessions": [], "notes": [],
                    "created": ts, "updated": ts, "_nk": 0, "_sk": 0,
                }
            it = items.get(i)
            if it is None:
                continue
            if op == "set":
                for k, v in ev.get("fields", {}).items():
                    it[k] = v
            elif op == "note":
                # ids are handed out in log order, so they stay stable on replay
                # even after earlier lines are deleted
                it["notes"].append({"id": "n%d" % it["_nk"], "ts": ts,
                                    "text": ev.get("text", ""), "_orig": ev.get("text", ""),
                                    "session": ev.get("session", ""), "edited": False})
                it["_nk"] += 1
            elif op == "note_edit":
                for n in it["notes"]:
                    if n["id"] == ev.get("line"):
                        n["text"] = ev.get("text", "")
            elif op == "note_del":
                it["notes"] = [n for n in it["notes"] if n["id"] != ev.get("line")]
            elif op == "sess":
                s = ev.get("session", "")
                if s and (not it["sessions"] or it["sessions"][-1]["name"] != s):
                    it["sessions"].append({"id": "s%d" % it["_sk"], "name": s, "ts": ts})
                    it["_sk"] += 1
            elif op == "sess_edit":
                for x in it["sessions"]:
                    if x["id"] == ev.get("line"):
                        x["name"] = ev.get("session", "")
            elif op == "sess_del":
                it["sessions"] = [x for x in it["sessions"] if x["id"] != ev.get("line")]
            elif op == "del":
                it["deleted"] = True
            it["updated"] = ts
    out = {}
    for k, v in items.items():
        if v.get("deleted"):
            continue
        v.pop("_nk", None)
        v.pop("_sk", None)
        for n in v["notes"]:              # edited only if it still differs from the original
            n["edited"] = n["text"] != n.pop("_orig", n["text"])
        out[k] = v
    return out


def session_meta():
    """Labels, tags and notes the user has attached to catalog sessions, keyed by
    session uuid. Written as `smeta` events; they carry `sid` rather than `id`
    so load() ignores them."""
    meta = {}
    if not os.path.exists(LOG):
        return meta
    with open(LOG) as f:
        for line in f:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("op") != "smeta" or not ev.get("sid"):
                continue
            m = meta.setdefault(ev["sid"], {"label": "", "tags": [], "note": "", "ts": ""})
            for k, v in (ev.get("fields") or {}).items():
                if k in SMETA_FIELDS:
                    m[k] = v
            m["ts"] = ev.get("ts", "")
    return meta


def thread_sessions(items):
    """session name (topic half) or resume token -> [thread ids], so the catalog
    can show which worklog threads ran in a session."""
    out = {}
    for it in items.values():
        names = {s["name"].rsplit("/", 1)[-1] for s in it["sessions"]}
        if it.get("resume"):
            names.add(it["resume"])
        for n in names:
            if n and it["id"] not in out.setdefault(n, []):
                out[n].append(it["id"])
    return out


def next_id(items):
    n = 0
    for k in items:
        if k.startswith("w") and k[1:].isdigit():
            n = max(n, int(k[1:]))
    return "w%d" % (n + 1)


def cur_session(it):
    return it["sessions"][-1]["name"] if it["sessions"] else ""


def account(it):
    """The account half of an 'account/topic' session name, if there is one."""
    s = cur_session(it)
    return s.rsplit("/", 1)[0] if "/" in s else ""


def resume_token(it):
    """What to pass to `claude --resume`. Explicit override wins, else the
    topic half of the session name (accounts live before the slash)."""
    if it.get("resume"):
        return it["resume"]
    s = cur_session(it)
    return s.rsplit("/", 1)[-1] if s else ""


def resume_cmd(it):
    tok = resume_token(it)
    return ('claude --resume "%s"' % tok) if tok else ""


def due_state(it, today=None):
    """'' | 'upcoming' | 'today' | 'overdue', plus days delta."""
    d = (it.get("due") or "").strip()
    if not d:
        return "", 0
    today = today or datetime.date.today().isoformat()
    if d < today:
        try:
            delta = (datetime.date.today() - datetime.date.fromisoformat(d)).days
        except ValueError:
            delta = 0
        return "overdue", delta
    return ("today", 0) if d == today else ("upcoming", 0)


def sort_key(it):
    """Priority band first, then the hand-dragged rank. Anything never dragged
    has rank None and keeps the old status/recency ordering, after the ranked."""
    p = PRIOS.index(it["prio"]) if it["prio"] in PRIOS else 9
    s = STATUSES.index(it["status"]) if it["status"] in STATUSES else 9
    r = it.get("rank")
    return (p, 1 if r is None else 0, r if r is not None else 0, s, it["updated"])


def handoff(it):
    """Markdown block to paste into a chat on another Claude account."""
    out = ["Picking up existing work item **%s** from another Claude session." % it["id"], ""]
    out.append("- **Title**: %s" % it["title"])
    out.append("- **Priority**: %s / **Status**: %s" % (it["prio"], it["status"]))
    if it.get("due"):
        out.append("- **Check on**: %s" % it["due"])
    if it["people"]:
        out.append("- **Working with**: %s" % ", ".join(it["people"]))
    if it["repo"]:
        out.append("- **Repo/area**: %s" % it["repo"])
    if it["tags"]:
        out.append("- **Tags**: %s" % ", ".join(it["tags"]))
    if it["sessions"]:
        out.append("- **Session history**: %s" % " -> ".join(
            "%s (%s)" % (s["name"], short(s["ts"])) for s in it["sessions"]))
    if resume_cmd(it):
        out.append("- **Resume the previous session with**: `%s`" % resume_cmd(it))
    if it["notes"]:
        out += ["", "**Log so far:**"]
        for n in it["notes"]:
            out.append("- %s: %s" % (short(n["ts"]), n["text"]))
    out += ["", "Tracker lives at `%s` (item %s) — append progress with "
            "`%s note %s \"...\"`." % (LOG, it["id"], os.path.join(HERE, "wl"), it["id"])]
    return "\n".join(out)
