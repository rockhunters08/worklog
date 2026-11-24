#!/usr/bin/env python3
"""wl - command-line access to the worklog. The web app (./serve.py) is the
main interface; this exists so Claude sessions and scripts can log without a
browser. Both write the same worklog.jsonl."""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import catalog
import term
from config import CFG
from store import (LOG, MD, OPEN, PRIOS, STATUSES, append, csv, cur_session,
                   due_state, handoff, load, next_id, now, resume_cmd, session_meta,
                   short, sort_key, thread_sessions)

HERE = os.path.dirname(os.path.realpath(__file__))


def env_session():
    return os.environ.get("WL_SESSION", "")


def resolve(items, key):
    key = key.lstrip("#")
    if key in items:
        return items[key]
    if ("w" + key) in items:
        return items["w" + key]
    hits = [it for it in items.values() if key.lower() in it["title"].lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit("wl: no item matching %r" % key)
    sys.exit("wl: %r is ambiguous: %s" % (key, ", ".join(h["id"] for h in hits)))


def cmd_add(a):
    items = load()
    i = next_id(items)
    append({"op": "add", "id": i, "title": a.title, "prio": a.prio})
    fields = {}
    if a.who:
        fields["people"] = csv(a.who)
    if a.tag:
        fields["tags"] = csv(a.tag)
    if a.repo:
        fields["repo"] = a.repo
    if fields:
        append({"op": "set", "id": i, "fields": fields})
    s = a.session or env_session()
    if s:
        append({"op": "sess", "id": i, "session": s})
    if a.note:
        append({"op": "note", "id": i, "text": a.note, "session": s})
    print("%s  %s  %s" % (i, a.prio, a.title))


def cmd_ls(a):
    rows = list(load().values())
    if not a.all:
        rows = [r for r in rows if r["status"] in ([a.status] if a.status else OPEN)]
    elif a.status:
        rows = [r for r in rows if r["status"] == a.status]
    if a.prio:
        rows = [r for r in rows if r["prio"] == a.prio.upper()]
    if a.who:
        rows = [r for r in rows if any(a.who.lower() in p.lower() for p in r["people"])]
    if a.tag:
        rows = [r for r in rows if any(a.tag.lower() == t.lower() for t in r["tags"])]
    if a.session:
        rows = [r for r in rows if any(a.session.lower() in s["name"].lower() for s in r["sessions"])]
    if a.due:
        rows = [r for r in rows if due_state(r)[0] in ("today", "overdue")]
    rows.sort(key=sort_key)
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("(nothing)")
        return
    w = min(max(max(len(r["title"]) for r in rows), 20), 56)
    fmt = "%-4s %-3s %-8s %-10s %-*s %-22s %s"
    print(fmt % ("ID", "PRI", "STATUS", "CHECK", w, "TITLE", "SESSION", "WITH"))
    for r in rows:
        t = r["title"] if len(r["title"]) <= w else r["title"][: w - 1] + "…"
        sess = cur_session(r)
        sess = sess if len(sess) <= 22 else sess[:21] + "…"
        st, days = due_state(r)
        chk = {"overdue": "!%dd late" % days, "today": "! today",
               "upcoming": r["due"][5:], "": ""}[st]
        print(fmt % (r["id"], r["prio"], r["status"], chk, w, t, sess,
                     ",".join(r["people"])))


def cmd_show(a):
    it = resolve(load(), a.id)
    print("%s  [%s] %s" % (it["id"], it["prio"], it["title"]))
    print("status   : %s" % it["status"])
    for label, val in (("with", ", ".join(it["people"])), ("tags", ", ".join(it["tags"])),
                       ("repo", it["repo"])):
        if val:
            print("%-9s: %s" % (label, val))
    if it.get("due"):
        st, days = due_state(it)
        print("check on : %s%s" % (it["due"],
              "  (OVERDUE by %dd)" % days if st == "overdue"
              else "  (TODAY)" if st == "today" else ""))
    if resume_cmd(it):
        print("resume   : %s" % resume_cmd(it))
    print("created  : %s" % short(it["created"]))
    print("updated  : %s" % short(it["updated"]))
    if it["sessions"]:
        print("sessions :")
        for s in it["sessions"]:
            print("  %s  %s" % (short(s["ts"]), s["name"]))
    if it["notes"]:
        print("notes    :")
        for n in it["notes"]:
            print("  %s%s  %s" % (short(n["ts"]),
                                  (" (%s)" % n["session"]) if n["session"] else "", n["text"]))


def cmd_note(a):
    it = resolve(load(), a.id)
    append({"op": "note", "id": it["id"], "text": " ".join(a.text),
            "session": a.session or env_session() or cur_session(it)})
    print("%s  note added" % it["id"])


def cmd_sess(a):
    it = resolve(load(), a.id)
    append({"op": "sess", "id": it["id"], "session": a.session})
    print("%s  now on %s" % (it["id"], a.session))


def cmd_set(a, field, value):
    it = resolve(load(), a.id)
    append({"op": "set", "id": it["id"], "fields": {field: value}})
    print("%s  %s = %s" % (it["id"], field, ",".join(value) if isinstance(value, list) else value))


def cmd_resume(a):
    it = resolve(load(), a.id)
    if getattr(a, "as_", None):
        append({"op": "set", "id": it["id"], "fields": {"resume": a.as_}})
        print('%s  resume token = %s' % (it["id"], a.as_))
        return
    print(handoff(it))


def cmd_log(a):
    items = load()
    it = resolve(items, a.id)
    lines = ([{"id": x["id"], "ts": x["ts"], "kind": "sess",
               "text": ("started in " if n == 0 else "moved to ") + x["name"]}
              for n, x in enumerate(it["sessions"])]
             + [{"id": n["id"], "ts": n["ts"], "kind": "note",
                 "text": n["text"] + ("  (edited)" if n.get("edited") else "")}
                for n in it["notes"]])
    lines.sort(key=lambda l: l["ts"], reverse=True)
    if not a.line:
        if not lines:
            print("(nothing logged)")
        for l in lines:
            print("%-4s %s  %s" % (l["id"], short(l["ts"]), l["text"]))
        return
    hit = next((l for l in lines if l["id"] == a.line), None)
    if not hit:
        sys.exit("wl: no log line %r on %s" % (a.line, it["id"]))
    hop = hit["kind"] == "sess"
    if a.rm:
        append({"op": "sess_del" if hop else "note_del", "id": it["id"], "line": a.line})
        print("%s  %s deleted" % (it["id"], a.line))
        return
    if not a.text:
        sys.exit("wl: give replacement text, or --rm")
    txt = " ".join(a.text)
    if hop:
        append({"op": "sess_edit", "id": it["id"], "line": a.line, "session": txt})
    else:
        append({"op": "note_edit", "id": it["id"], "line": a.line, "text": txt})
    print("%s  %s updated" % (it["id"], a.line))


def cmd_due(a):
    items = load()
    if not a.id:  # no args: what needs checking today
        rows = [r for r in items.values() if due_state(r)[0] in ("today", "overdue")
                and r["status"] not in ("done",)]
        if not rows:
            print("nothing to check today")
            return
        for r in sorted(rows, key=lambda r: r["due"]):
            st, days = due_state(r)
            print("%-4s %-3s %s  %s%s" % (r["id"], r["prio"], r["due"], r["title"],
                  "  (%dd late)" % days if st == "overdue" else ""))
            if resume_cmd(r):
                print("          %s" % resume_cmd(r))
        return
    it = resolve(items, a.id)
    if not a.date:
        st, days = due_state(it)
        print("%s  %s%s" % (it["id"], it["due"] or "(no check date)",
              "  OVERDUE by %dd" % days if st == "overdue" else ""))
        return
    val = "" if a.date in ("clear", "none", "-") else a.date
    if val:
        import datetime as _dt
        try:
            _dt.date.fromisoformat(val)
        except ValueError:
            sys.exit("wl: date must be YYYY-MM-DD (or 'clear')")
    append({"op": "set", "id": it["id"], "fields": {"due": val}})
    print("%s  check on %s" % (it["id"], val or "(cleared)"))


def cmd_sessions(a):
    seen = {}
    for it in load().values():
        for s in it["sessions"]:
            e = seen.setdefault(s["name"], {"last": s["ts"], "ids": []})
            e["last"] = max(e["last"], s["ts"])
            if it["id"] not in e["ids"]:
                e["ids"].append(it["id"])
    if not seen:
        print("(no sessions recorded)")
        return
    for name, e in sorted(seen.items(), key=lambda kv: kv[1]["last"], reverse=True):
        print("%-28s  %s  %s" % (name, short(e["last"]), " ".join(e["ids"])))


def cmd_md(a):
    rows = sorted(load().values(), key=sort_key)
    out = ["# Worklog", "", "_Generated %s by `wl md`. Source of truth is `worklog.jsonl`._"
           % short(now()), ""]
    for status in STATUSES:
        group = [r for r in rows if r["status"] == status]
        if not group:
            continue
        out += ["## %s" % status.title(), "",
                "| ID | Pri | Title | Session | With | Updated |", "|---|---|---|---|---|---|"]
        for r in group:
            out.append("| %s | %s | %s | %s | %s | %s |" % (
                r["id"], r["prio"], r["title"], cur_session(r) or "-",
                ", ".join(r["people"]) or "-", short(r["updated"])))
        out.append("")
    open(MD, "w").write("\n".join(out) + "\n")
    print("wrote %s (%d items)" % (MD, len(rows)))


# ---------------------------------------------------------------- catalog

def catalog_rows(q=""):
    cat = catalog.scan()
    return cat, catalog.rows(cat, session_meta(), thread_sessions(load()), q)


def pick_session(cat, key):
    hits = catalog.resolve(cat, key)
    if not hits:
        sys.exit("wl: no session matching %r (uuid, uuid prefix, or session name)" % key)
    if len(hits) > 1 and not catalog.UUID.match(key):
        print("wl: %r names %d sessions, taking the most recent (%s); use the uuid to pick another"
              % (key, len(hits), short(hits[0].get("last", ""))), file=sys.stderr)
    return hits[0]


def cmd_catalog(a):
    cat, rows = catalog_rows(" ".join(a.query))
    if not a.all:
        rows = [r for r in rows if not r["tiny"]]
    if a.project:
        rows = [r for r in rows if a.project in r["project"]]
    if a.unlabelled:
        rows = [r for r in rows if not r["label"]]
    if a.tag:
        rows = [r for r in rows if a.tag in r["tags"] or a.tag in r["auto_tags"]]
    if a.who:
        rows = [r for r in rows if any(a.who.lower() in p.lower() for p in r["people"])]
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("(no sessions)")
        return
    for r in rows:
        name = r["label"] or r["auto_label"] or r["title"] or r["ai_title"] or r["first_prompt"][:60].replace("\n", " ")
        extra = []
        if r["label"] and r["title"]:
            extra.append(r["title"])
        if r["project"] != "~/code":
            extra.append(r["project"])
        if r["threads"]:
            extra.append(" ".join(r["threads"]))
        if r["shared"]:
            extra.append("name shared by %d" % (r["shared"] + 1))
        print("%s  %s  %-44s %5d  %s" % (r["sid"][:8], short(r["last"]), name[:44],
                                         r["n_prompts"], "  ".join(extra)))
        if r["summary_ai"] and not a.brief:
            print("          %s" % r["summary_ai"][:220])
        if r["snippet"]:
            print("          > %s" % r["snippet"])
    print("%d session%s" % (len(rows), "" if len(rows) == 1 else "s"))


def cmd_session(a):
    cat, rows = catalog_rows()
    e = pick_session(cat, a.key)
    r = next(x for x in rows if x["sid"] == e["sid"])
    print("%s  %s" % (r["sid"], r["label"] or (r["auto_label"] + "  (auto)" if r["auto_label"] else "(no label)")))
    for k in ("title", "ai_title", "summary", "project", "branch"):
        if r[k]:
            print("%-9s: %s" % (k.replace("_", " "), r[k]))
    if len(r["titles"]) > 1:
        print("names    : %s" % " -> ".join(r["titles"]))
    if r["summary_ai"]:
        print("what     : %s" % r["summary_ai"])
    if r["outcome"]:
        print("ended    : %s" % r["outcome"])
    if r["prs"]:
        print("prs      : %s" % ", ".join(catalog.short_pr(u) for u in r["prs"]))
    if r["issues"]:
        print("tickets  : %s" % ", ".join(r["issues"]))
    if r["repos"]:
        print("repos    : %s" % ", ".join(r["repos"]))
    if r["people"]:
        print("people   : %s" % ", ".join(r["people"]))
    if r["tags"] or r["auto_tags"]:
        print("tags     : %s" % ", ".join(r["tags"] + [t for t in r["auto_tags"] if t not in r["tags"]]))
    if r["note"]:
        print("note     : %s" % r["note"])
    if r["threads"]:
        print("threads  : %s" % " ".join(r["threads"]))
    print("activity : %s .. %s  (%d prompts, %d KB)" % (short(r["created"]), short(r["last"]),
                                                       r["n_prompts"], r["size"] // 1024))
    print("account  : %s%s" % (r["account"] or "(default)",
          "" if r["account_known"] else "  (not recorded; default assumed)") +
          ("  NO CONFIG DIR: run `wl account add %s`" % r["account"] if r["account_state"] == "unmapped" else ""))
    if r["resume_error"]:
        print("resume   : (none) %s" % r["resume_error"])
    else:
        print("resume   : %s" % r["resume_cmd"])
        if r["title"]:
            print("by uuid  : %s" % r["resume_uuid_cmd"])
    if r["shared"]:
        print("NOTE     : %d other session%s carry the name %r; by-name resume picks one of them"
              % (r["shared"], "" if r["shared"] == 1 else "s", r["title"]))
    print("file     : %s" % r["path"])
    if r["first_prompt"]:
        print("first    : %s" % r["first_prompt"][:400].replace("\n", " "))


def cmd_label(a):
    cat = catalog.scan()
    e = pick_session(cat, a.key)
    fields = {}
    if a.label is not None:
        fields["label"] = a.label
    if a.tag is not None:
        fields["tags"] = csv(a.tag)
    if a.note is not None:
        fields["note"] = a.note
    if not fields:
        sys.exit("wl: give a label, --tag or --note")
    append({"op": "smeta", "sid": e["sid"], "fields": fields})
    print("%s  %s" % (e["sid"][:8], "  ".join("%s = %s" % (k, v) for k, v in fields.items())))


def cmd_launch(a):
    """Open the session in the configured terminal, under its own account."""
    cat = catalog.scan()
    e = pick_session(cat, a.key)
    try:
        argv, cwd, env = catalog.launch_argv(e)
    except catalog.AccountError as ex:
        sys.exit("wl: %s" % ex)
    if a.print_only:
        argv, cwd, env = term.wrapped_argv(e["sid"], argv, cwd, env)
        print(term.shell_cmd(argv, cwd, env))
        return
    line = term.open_in_terminal(e["sid"], argv, cwd, env)
    print("%s  opened in %s%s\n  %s" % (e["sid"][:8], CFG["launch"]["terminal"],
                                         " (tmux %s)" % term.tmux_name(e["sid"]) if term.tmux() else "", line))


def cmd_account(a):
    import accounts
    if a.what == "ls":
        for x in accounts.listing():
            print("%-32s %-9s %s%s" % (x["email"] or "(unknown)", "ok" if x["logged_in"] else "LOGGED OUT",
                                       x["dir"], "  (default)" if x["default"] else
                                       ("  logged in as %s" % x["actual"] if x["actual"] and x["actual"] != x["email"] else "")))
        return
    if a.what == "add":
        if not a.email:
            sys.exit("wl account add <email> [alias]")
        alias = a.alias or a.email.replace("@", "-at-").replace("+", "-")
        d = os.path.join(accounts.ACCOUNTS_ROOT, alias)
        try:
            accounts.record(a.email, d)             # validate before touching the filesystem
        except ValueError as ex:
            sys.exit("wl: %s" % ex)
        accounts.make_dir(alias)
        print("account dir %s (symlinks over ~/.claude); recorded in %s" % (d, accounts.CONFIG_PATH))
        print("now log it in, once:\n\n  %s\n" % accounts.login_cmd(a.email, d))
        return
    sys.exit("wl account ls | add <email> [alias]")


def cmd_rm(a):
    it = resolve(load(), a.id)
    append({"op": "del", "id": it["id"]})
    print("%s deleted" % it["id"])


def main():
    p = argparse.ArgumentParser(prog="wl", description="worklog CLI (web app: ./serve.py)")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="add a work item")
    a.add_argument("title")
    a.add_argument("-p", "--prio", default="P2", choices=PRIOS)
    a.add_argument("-s", "--session", help="account/session, e.g. work/carplay-fix")
    a.add_argument("-w", "--who", help="comma-separated people")
    a.add_argument("-t", "--tag", help="comma-separated tags")
    a.add_argument("-r", "--repo", help="repo or area")
    a.add_argument("-n", "--note", help="opening note")
    a.set_defaults(fn=cmd_add)

    l = sub.add_parser("ls", help="list items (open ones by default)")
    l.add_argument("--all", action="store_true")
    l.add_argument("--status", choices=STATUSES)
    l.add_argument("-p", "--prio")
    l.add_argument("-w", "--who")
    l.add_argument("-t", "--tag")
    l.add_argument("-s", "--session")
    l.add_argument("--due", action="store_true", help="only items due today or overdue")
    l.add_argument("--json", action="store_true")
    l.set_defaults(fn=cmd_ls)

    s = sub.add_parser("show", help="full detail for one item")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)

    n = sub.add_parser("note", help="append a progress note")
    n.add_argument("id")
    n.add_argument("text", nargs="+")
    n.add_argument("-s", "--session")
    n.set_defaults(fn=cmd_note)

    ss = sub.add_parser("sess", help="record a move to another session/account")
    ss.add_argument("id")
    ss.add_argument("session")
    ss.set_defaults(fn=cmd_sess)

    pr = sub.add_parser("pri", help="change priority")
    pr.add_argument("id")
    pr.add_argument("prio", choices=PRIOS)
    pr.set_defaults(fn=lambda a: cmd_set(a, "prio", a.prio))

    st = sub.add_parser("status", help="change status")
    st.add_argument("id")
    st.add_argument("status", choices=STATUSES)
    st.set_defaults(fn=lambda a: cmd_set(a, "status", a.status))

    dn = sub.add_parser("done", help="mark done")
    dn.add_argument("id")
    dn.set_defaults(fn=lambda a: cmd_set(a, "status", "done"))

    wh = sub.add_parser("who", help="set collaborators")
    wh.add_argument("id")
    wh.add_argument("people")
    wh.set_defaults(fn=lambda a: cmd_set(a, "people", csv(a.people)))

    tg = sub.add_parser("tag", help="set tags")
    tg.add_argument("id")
    tg.add_argument("tags")
    tg.set_defaults(fn=lambda a: cmd_set(a, "tags", csv(a.tags)))

    rp = sub.add_parser("repo", help="set repo/area")
    rp.add_argument("id")
    rp.add_argument("repo")
    rp.set_defaults(fn=lambda a: cmd_set(a, "repo", a.repo))

    rm = sub.add_parser("rm", help="delete an item")
    rm.add_argument("id")
    rm.set_defaults(fn=cmd_rm)

    rs = sub.add_parser("resume", help="print a handoff block; --as sets the --resume token")
    rs.add_argument("id")
    rs.add_argument("--as", dest="as_", metavar="TOKEN",
                    help='what `claude --resume` should be given (default: the session name)')
    rs.set_defaults(fn=cmd_resume)

    lg = sub.add_parser("log", help="list log lines; or edit/delete one by its id")
    lg.add_argument("id")
    lg.add_argument("line", nargs="?", help="line id, e.g. n0 or s1")
    lg.add_argument("text", nargs="*", help="replacement text (a session name for s-lines)")
    lg.add_argument("--rm", action="store_true", help="delete the line instead")
    lg.set_defaults(fn=cmd_log)

    du = sub.add_parser("due", help="set/show a check-on date; no args lists what is due")
    du.add_argument("id", nargs="?")
    du.add_argument("date", nargs="?", help="YYYY-MM-DD, or 'clear'")
    du.set_defaults(fn=cmd_due)

    se = sub.add_parser("sessions", help="list every account/session seen")
    se.set_defaults(fn=cmd_sessions)

    ct = sub.add_parser("catalog", help="every Claude session on this Mac; words = search")
    ct.add_argument("query", nargs="*")
    ct.add_argument("--all", action="store_true", help="include tiny sessions")
    ct.add_argument("--project", help="substring of the project path, e.g. rock")
    ct.add_argument("--unlabelled", action="store_true")
    ct.add_argument("-t", "--tag", help="a tag (yours or auto)")
    ct.add_argument("-w", "--who", help="a person's name")
    ct.add_argument("--brief", action="store_true", help="one line per session, no summary")
    ct.add_argument("--json", action="store_true")
    ct.set_defaults(fn=cmd_catalog)

    sn = sub.add_parser("session", help="detail for one catalog session (uuid, prefix or name)")
    sn.add_argument("key")
    sn.set_defaults(fn=cmd_session)

    lb = sub.add_parser("label", help="label / tag / annotate a catalog session")
    lb.add_argument("key")
    lb.add_argument("label", nargs="?")
    lb.add_argument("-t", "--tag", help="comma-separated tags")
    lb.add_argument("-n", "--note")
    lb.set_defaults(fn=cmd_label)

    la = sub.add_parser("launch", help="open a catalog session in your terminal, under its own account")
    la.add_argument("key")
    la.add_argument("-p", "--print", dest="print_only", action="store_true", help="print the shell line instead")
    la.set_defaults(fn=cmd_launch)

    ac = sub.add_parser("account", help="ls | add <email> [alias]: Claude logins the catalog can resume under")
    ac.add_argument("what", choices=["ls", "add"])
    ac.add_argument("email", nargs="?")
    ac.add_argument("alias", nargs="?")
    ac.set_defaults(fn=cmd_account)

    md = sub.add_parser("md", help="regenerate WORKLOG.md")
    md.set_defaults(fn=cmd_md)

    args = p.parse_args()
    if not getattr(args, "fn", None):
        p.print_help()
        return
    args.fn(args)


if __name__ == "__main__":
    main()
