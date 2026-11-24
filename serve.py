#!/usr/bin/env python3
"""Local web app for the worklog. Binds to 127.0.0.1 only.

    ./serve.py            # http://127.0.0.1:8787
    ./serve.py -p 9000 -o # different port, open the browser
"""
import argparse
import datetime
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import catalog
import store
import term
from config import CFG

HERE = os.path.dirname(os.path.realpath(__file__))
PAGE = os.path.join(HERE, "index.html")
STATIC = os.path.join(HERE, "static")
# Anything that runs a command needs this token. It is minted per server start
# and injected into index.html, which another origin can request but never
# read, so a web page you happen to have open cannot reach the spawn endpoints.
TOKEN = secrets.token_urlsafe(24)
PORT = [CFG["server"]["port"]]


def page_version():
    """mtime of index.html; the page reloads itself when this changes, so a
    tab left open never keeps running an old copy after an upgrade."""
    try:
        return int(os.stat(PAGE).st_mtime)
    except OSError:
        return 0


def state():
    items = sorted(store.load().values(), key=store.sort_key)
    sessions, people, tags = {}, set(), set()
    for it in items:
        it["resume_cmd"] = store.resume_cmd(it)
        it["account"] = store.account(it)
        people.update(it["people"])
        tags.update(it["tags"])
        for s in it["sessions"]:
            sessions[s["name"]] = max(sessions.get(s["name"], ""), s["ts"])
    return {
        "items": items,
        "sessions": [k for k, _ in sorted(sessions.items(), key=lambda kv: kv[1], reverse=True)],
        "people": sorted(people),
        "tags": sorted(tags),
        "prios": store.PRIOS,
        "statuses": store.STATUSES,
        "log": store.LOG,
        "today": datetime.date.today().isoformat(),
        "page": page_version(),
    }


class Enricher:
    """Runs enrich.py as a subprocess, one at a time, so summaries keep up with
    the transcripts on disk without anyone remembering to run the script."""
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.args = []
        self.last = 0.0
        self.error = ""

    def running(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return True
            if self.proc:
                if self.proc.returncode:
                    self.error = "enrich.py exited %d" % self.proc.returncode
                self.proc = None
            return False

    def start(self, sids=()):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return False
            self.args = list(sids)
            self.error = ""
            self.last = time.time()
            self.proc = subprocess.Popen(
                [sys.executable, os.path.join(HERE, "enrich.py")] + self.args,
                stdout=open(os.path.join(HERE, "enrich.log"), "a"), stderr=subprocess.STDOUT,
                cwd=HERE)
            return True


ENRICHER = Enricher()


def sessions(q="", force=False):
    cat, scanning = catalog.ensure(force=force)
    items = store.load()
    rows = catalog.rows(cat, store.session_meta(), store.thread_sessions(items), q)
    st = catalog.status()
    meta = store.session_meta()
    stale = [e for e in cat.values() if catalog.needs_summary(e, meta.get(e["sid"], {}))]
    fresh = [e["sid"] for e in stale if not meta.get(e["sid"], {}).get("enriched")]
    enriching = ENRICHER.running()
    # auto mode: a never-summarised session is done as soon as it is seen;
    # grown ones wait ten minutes between runs so a session being typed into
    # right now is not re-summarised on every poll
    if CFG["enrich"]["auto"] and stale and not enriching and not scanning:
        if fresh:
            enriching = ENRICHER.start(fresh)
        elif time.time() - ENRICHER.last > 600:
            enriching = ENRICHER.start()
    stale = len(stale)
    return {"items": rows, "q": q, "scanning": scanning, "done": st["done"],
            "total": st["total"], "error": st["error"] or ENRICHER.error,
            "projects_dir": catalog.PROJECTS, "github_org": CFG["github"]["org"],
            "unsummarised": stale, "enriching": enriching, "enrich_auto": CFG["enrich"]["auto"],
            "active_hours": CFG["catalog"]["active_hours"], "page": page_version(),
            "code_root": catalog.CODE_ROOT_SHORT, "tmux": bool(term.tmux()),
            "terminal_app": CFG["launch"]["terminal"],
            "today": datetime.date.today().isoformat()}


def smeta(ev):
    sid = (ev.get("sid") or "").strip()
    if not catalog.UUID.match(sid):
        return None, "sid must be a session uuid"
    fields = ev.get("fields") or {}
    allowed = {}
    if "label" in fields:
        allowed["label"] = (fields["label"] or "").strip()
    if "tags" in fields:
        allowed["tags"] = fields["tags"] if isinstance(fields["tags"], list) else store.csv(fields["tags"])
    if "note" in fields:
        allowed["note"] = (fields["note"] or "").strip()
    if not allowed:
        return None, "nothing to set"
    store.append({"op": "smeta", "sid": sid, "fields": allowed})
    return allowed, ""


class Handler(BaseHTTPRequestHandler):
    server_version = "worklog"

    def log_message(self, fmt, *args):
        pass  # quiet

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return origin in ("http://127.0.0.1:%d" % PORT[0], "http://localhost:%d" % PORT[0])

    def _authorised(self, qs=None):
        tok = self.headers.get("X-WL-Token") or (qs or {}).get("token", [""])[0]
        return self._same_origin() and secrets.compare_digest(tok or "", TOKEN)

    def _static(self, path):
        name = os.path.normpath(path[len("/static/"):])
        full = os.path.join(STATIC, name)
        if name.startswith("..") or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def _terminal(self, qs):
        """GET /ws/term?sid=..&token=..&cols=..&rows=..: a WebSocket pty."""
        if not self._authorised(qs):
            return self._send(403, {"error": "forbidden"})
        sid = qs.get("sid", [""])[0]
        e = catalog.ensure()[0].get(sid)
        if not e:
            return self._send(404, {"error": "no such session"})
        argv, cwd, env = catalog.launch_argv(e)
        try:
            sock = term.handshake(self)
        except ValueError as ex:
            return self._send(400, {"error": str(ex)})
        self.close_connection = True
        term.serve_pty(sock, sid, argv, cwd, env,
                       cols=int(qs.get("cols", ["120"])[0]), rows=int(qs.get("rows", ["36"])[0]))

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                with open(PAGE) as f:
                    html = f.read().replace("<head>", '<head>\n<meta name="wl-token" content="%s">' % TOKEN, 1)
                    return self._send(200, html, "text/html")
            except OSError:
                return self._send(500, {"error": "index.html missing"})
        if path.startswith("/static/"):
            return self._static(path)
        if path == "/ws/term":
            return self._terminal(parse_qs(self.path.split("?", 1)[-1]) if "?" in self.path else {})
        if path == "/api/state":
            return self._send(200, state())
        if path == "/api/sessions":
            qs = parse_qs(self.path.split("?", 1)[-1]) if "?" in self.path else {}
            return self._send(200, sessions(q=(qs.get("q") or [""])[0],
                                            force=bool(qs.get("rescan"))))
        if path == "/api/handoff":
            qs = dict(p.split("=", 1) for p in self.path.split("?", 1)[-1].split("&") if "=" in p)
            it = store.load().get(qs.get("id", ""))
            if not it:
                return self._send(404, {"error": "no such item"})
            return self._send(200, {"text": store.handoff(it)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/event":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            ev = json.loads(self.rfile.read(n) or "{}")
        except (ValueError, TypeError):
            return self._send(400, {"error": "bad json"})

        op = ev.get("op")
        if op == "smeta":
            fields, err = smeta(ev)
            if err:
                return self._send(400, {"error": err})
            return self._send(200, sessions(q=ev.get("q", "")))
        if op == "launch":
            if not self._authorised():
                return self._send(403, {"error": "forbidden"})
            sid = (ev.get("sid") or "").strip()
            e = catalog.ensure()[0].get(sid)
            if not e:
                return self._send(404, {"error": "no such session"})
            argv, cwd, env = catalog.launch_argv(e)
            try:
                line = term.open_in_terminal(sid, argv, cwd, env)
            except Exception as ex:
                return self._send(500, {"error": str(ex)[:300]})
            return self._send(200, {"ok": True, "command": line, "tmux": bool(term.tmux())})
        if op == "enrich":
            sid = (ev.get("sid") or "").strip()
            if sid and not catalog.UUID.match(sid):
                return self._send(400, {"error": "sid must be a session uuid"})
            if not ENRICHER.start([sid, "--force"] if sid else []):
                return self._send(409, {"error": "a summarise run is already going"})
            return self._send(200, sessions(q=ev.get("q", "")))
        if op == "add":
            title = (ev.get("title") or "").strip()
            if not title:
                return self._send(400, {"error": "title required"})
            items = store.load()
            i = store.next_id(items)
            prio = ev.get("prio") if ev.get("prio") in store.PRIOS else "P2"
            store.append({"op": "add", "id": i, "title": title, "prio": prio})
            fields = {}
            for k in ("people", "tags"):
                if ev.get(k):
                    fields[k] = ev[k] if isinstance(ev[k], list) else store.csv(ev[k])
            for k in ("repo", "due", "resume"):
                if ev.get(k):
                    fields[k] = ev[k]
            if fields:
                store.append({"op": "set", "id": i, "fields": fields})
            if ev.get("session"):
                store.append({"op": "sess", "id": i, "session": ev["session"]})
            if ev.get("note"):
                store.append({"op": "note", "id": i, "text": ev["note"],
                              "session": ev.get("session", "")})
            return self._send(200, state())

        if op == "reorder":
            order = ev.get("order") or []
            if not isinstance(order, list) or not order:
                return self._send(400, {"error": "order required"})
            items = store.load()
            moved = ev.get("id")
            if moved in items and ev.get("prio") in store.PRIOS \
                    and items[moved]["prio"] != ev["prio"]:
                store.append({"op": "set", "id": moved, "fields": {"prio": ev["prio"]}})
            for n, oid in enumerate(order):          # only write ranks that moved
                it = items.get(oid)
                if it is not None and it.get("rank") != n * 10:
                    store.append({"op": "set", "id": oid, "fields": {"rank": n * 10}})
            return self._send(200, state())

        i = ev.get("id")
        if not i or i not in store.load():
            return self._send(404, {"error": "no such item"})
        if op == "set":
            fields = ev.get("fields") or {}
            if "prio" in fields and fields["prio"] not in store.PRIOS:
                return self._send(400, {"error": "bad prio"})
            if "status" in fields and fields["status"] not in store.STATUSES:
                return self._send(400, {"error": "bad status"})
            if "due" in fields and fields["due"]:
                try:
                    datetime.date.fromisoformat(fields["due"])
                except ValueError:
                    return self._send(400, {"error": "due must be YYYY-MM-DD"})
            allowed = {k: v for k, v in fields.items()
                       if k in ("prio", "status", "people", "tags", "repo", "title",
                                "due", "resume", "rank")}
            if not allowed:
                return self._send(400, {"error": "nothing to set"})
            store.append({"op": "set", "id": i, "fields": allowed})
        elif op == "note":
            if not (ev.get("text") or "").strip():
                return self._send(400, {"error": "empty note"})
            store.append({"op": "note", "id": i, "text": ev["text"].strip(),
                          "session": ev.get("session", "")})
        elif op == "sess":
            if not (ev.get("session") or "").strip():
                return self._send(400, {"error": "empty session"})
            store.append({"op": "sess", "id": i, "session": ev["session"].strip()})
        elif op in ("note_edit", "sess_edit"):
            line = (ev.get("line") or "").strip()
            val = (ev.get("text") if op == "note_edit" else ev.get("session")) or ""
            if not line or not val.strip():
                return self._send(400, {"error": "line and text required"})
            key = "text" if op == "note_edit" else "session"
            store.append({"op": op, "id": i, "line": line, key: val.strip()})
        elif op in ("note_del", "sess_del"):
            if not (ev.get("line") or "").strip():
                return self._send(400, {"error": "line required"})
            store.append({"op": op, "id": i, "line": ev["line"].strip()})
        elif op == "del":
            store.append({"op": "del", "id": i})
        else:
            return self._send(400, {"error": "unknown op"})
        return self._send(200, state())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port", type=int, default=int(os.environ.get("WL_PORT", CFG["server"]["port"])))
    ap.add_argument("-o", "--open", action="store_true", help="open a browser")
    a = ap.parse_args()
    url = "http://127.0.0.1:%d/" % a.port
    PORT[0] = a.port
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    print("worklog  %s\nlog      %s\nCtrl-C to stop" % (url, store.LOG))
    if a.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
