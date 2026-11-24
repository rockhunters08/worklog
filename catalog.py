"""Catalog of every Claude Code session on this Mac, built from the transcripts
under ~/.claude/projects and cached in sessions-cache.json.

A transcript is append-only, so a rescan only reads the bytes added since the
last pass; the first scan over everything takes a minute or two and runs in a
background thread so the web app stays responsive.

Session names (`claude --resume "<name>"`) are the `custom-title` records inside
the transcript. Every name a session has ever had is kept as an alias; the last
one written is the current name.
"""
import glob
import json
import os
import re
import threading
import time

from config import CFG

HERE = os.path.dirname(os.path.realpath(__file__))
PROJECTS = CFG["paths"]["projects"]
CODE_ROOT = CFG["paths"]["code_root"]
CACHE = CFG["paths"]["cache"]
HOME = os.path.expanduser("~")

PROMPT_CHARS = 400     # per prompt kept for search
PROMPT_MAX = 1200      # prompts kept per session
FIRST_CHARS = 1500
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_lock = threading.Lock()
_state = {"cat": None, "scanning": False, "done": 0, "total": 0, "checked": 0.0, "error": ""}


def _empty(sid, path):
    return {"sid": sid, "path": path, "proj_dir": os.path.basename(os.path.dirname(path)),
            "cwd": "", "size": 0, "mtime": 0, "offset": 0, "created": "", "last": "",
            "titles": [], "ai_title": "", "summary": "", "first_prompt": "",
            "n_prompts": 0, "prompts": [], "branch": "", "prs": [], "repos": {},
            "issues": [], "days": [], "last_reply": "", "account": ""}


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text")
    return ""


_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_EMAIL = re.compile(rb'"userEmail":"The user\'s email address is ([^\s"\\]+?)\.?[\s"\\]')
_CODE_PATH = re.compile(rb'"file_path":\s*"' + re.escape(CODE_ROOT.encode()) + rb'/([A-Za-z0-9_.-]+)/')
_ISSUE = re.compile("|".join("(?:%s)" % p for p in CFG["tickets"]["patterns"]))


def _clean(t):
    """What the user actually typed: reminders the harness prepends are dropped, and
    anything that is wholly a tag (task notifications, `!` shell output, slash
    command echoes) is not a prompt at all."""
    t = _REMINDER.sub("", t).strip()
    if (not t or t.startswith("<") or t.startswith("Caveat: The messages below")
            or t.startswith("[Request interrupted")):
        return ""
    return t


def _parse(entry, fh):
    """Read records from the current offset to EOF into the entry."""
    fh.seek(entry["offset"])
    for line in fh:
        if not line.endswith(b"\n"):
            break                                   # a partial write; pick it up next pass
        entry["offset"] += len(line)
        if b'"custom-title"' in line:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = r.get("customTitle") if r.get("type") == "custom-title" else None
            if t and (not entry["titles"] or entry["titles"][-1] != t):
                if t in entry["titles"]:
                    entry["titles"].remove(t)
                entry["titles"].append(t)
            continue
        if b'"ai-title"' in line:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "ai-title" and r.get("aiTitle"):
                entry["ai_title"] = r["aiTitle"]
            continue
        if b'"userEmail"' in line:
            m = _EMAIL.search(line)
            if m:
                entry["account"] = m.group(1).decode().lower()   # last one wins: the login it last ran under
            continue
        if b'"pr-link"' in line:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") == "pr-link" and r.get("prUrl") and r["prUrl"] not in entry["prs"]:
                entry["prs"].append(r["prUrl"])
            continue
        if b'"type":"assistant"' in line:
            for m in _CODE_PATH.findall(line):
                k = m.decode()
                entry["repos"][k] = entry["repos"].get(k, 0) + 1
            if b'"type":"text"' in line and b'"tool_use"' not in line:
                entry["_last_assistant"] = line
            continue
        if line.startswith(b'{"type":"summary"'):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("summary"):
                entry["summary"] = r["summary"]
            continue
        if b'"type":"user"' not in line or b'"tool_result"' in line \
                or b'"isSidechain":true' in line or b'"isMeta":true' in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") != "user":
            continue
        ts = r.get("timestamp", "")
        if ts:
            entry["created"] = entry["created"] or ts
            entry["last"] = max(entry["last"], ts)
            if ts[:10] not in entry["days"]:
                entry["days"].append(ts[:10])
        if r.get("cwd") and not entry["cwd"]:
            entry["cwd"] = r["cwd"]
        if r.get("gitBranch"):
            entry["branch"] = r["gitBranch"]
        t = _clean(_text((r.get("message") or {}).get("content")))
        if not t:
            continue
        entry["n_prompts"] += 1
        if not entry["first_prompt"]:
            entry["first_prompt"] = t[:FIRST_CHARS]
        if len(entry["prompts"]) < PROMPT_MAX:
            entry["prompts"].append(t[:PROMPT_CHARS])
        for mt in _ISSUE.finditer(t):
            k = mt.group(0)
            if k not in entry["issues"]:
                entry["issues"].append(k)
    raw = entry.pop("_last_assistant", None)
    if raw:
        try:
            r = json.loads(raw)
            t = _text((r.get("message") or {}).get("content")).strip()
            if t:
                entry["last_reply"] = t[:1200]
        except ValueError:
            pass


def _load_cache():
    try:
        with open(CACHE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cat):
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cat, f)
    os.replace(tmp, CACHE)


def _side_title(path):
    """custom-title.json next to the subagent dir; written on rename, but not
    always updated when a session is renamed back, so it is an alias only."""
    try:
        with open(os.path.join(path[:-6], "custom-title.json")) as f:
            return (json.load(f).get("customTitle") or "").strip()
    except (OSError, ValueError):
        return ""


def scan(progress=None):
    """Bring the cache up to date with the transcripts on disk. Returns the catalog."""
    cat = _load_cache()
    paths = sorted(glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")))
    seen = set()
    for n, path in enumerate(paths):
        sid = os.path.basename(path)[:-6]
        if not UUID.match(sid):
            continue
        seen.add(sid)
        try:
            st = os.stat(path)
        except OSError:
            continue
        e = cat.get(sid)
        if e is None or e.get("path") != path or st.st_size < e.get("offset", 0) \
                or "account" not in e:                 # cache written before a field existed: reread
            e = _empty(sid, path)
        if st.st_size > e["offset"] or st.st_mtime != e["mtime"]:
            try:
                with open(path, "rb") as fh:
                    _parse(e, fh)
            except OSError:
                continue
            e["size"], e["mtime"] = st.st_size, st.st_mtime
            side = _side_title(path)
            if side and side not in e["titles"]:
                e["titles"].insert(0, side)
            cat[sid] = e
        if progress:
            progress(n + 1, len(paths))
    for sid in list(cat):
        if sid not in seen:
            del cat[sid]
    _save_cache(cat)
    return cat


def _scan_bg():
    def prog(done, total):
        _state["done"], _state["total"] = done, total
    try:
        cat = scan(prog)
        with _lock:
            _state["cat"] = cat
            _state["error"] = ""
    except Exception as ex:                        # keep the server alive whatever happens
        _state["error"] = "%s: %s" % (type(ex).__name__, ex)
    finally:
        _state["scanning"] = False
        _state["checked"] = time.time()


def ensure(max_age=60, force=False):
    """Return (catalog, scanning). The first scan over an empty cache runs in
    a background thread; after that an incremental pass only reads bytes
    appended since last time, so it runs inline when older than max_age."""
    with _lock:
        if _state["cat"] is None:
            _state["cat"] = _load_cache()
        stale = force or (time.time() - _state["checked"] > max_age)
        if stale and not _state["scanning"]:
            if not _state["cat"]:
                _state["scanning"] = True
                _state["done"] = _state["total"] = 0
                threading.Thread(target=_scan_bg, daemon=True).start()
            else:
                try:
                    _state["cat"] = scan()
                    _state["error"] = ""
                except Exception as ex:
                    _state["error"] = "%s: %s" % (type(ex).__name__, ex)
                _state["checked"] = time.time()
        return _state["cat"], _state["scanning"]


def status():
    return {"scanning": _state["scanning"], "done": _state["done"],
            "total": _state["total"], "error": _state["error"],
            "checked": _state["checked"]}


# ---------------------------------------------------------------- presentation

def _short(path):
    return "~" + path[len(HOME):] if path.startswith(HOME) else path


CODE_ROOT_SHORT = _short(CODE_ROOT)


def project_of(e):
    """'~/code', '~/code/rock', ... from the recorded cwd, else from the dir name."""
    cwd = e.get("cwd") or ""
    if not cwd:
        d = e.get("proj_dir", "")
        cwd = d.replace("-", "/") if d.startswith("-") else d
    return _short(cwd) or "?"


def title_of(e):
    return e["titles"][-1] if e.get("titles") else ""


def is_tiny(e):
    return e.get("n_prompts", 0) < 2 and e.get("size", 0) < 60000


def resume_cmd(e, by_uuid=False):
    tok = e["sid"] if by_uuid or not title_of(e) else title_of(e)
    cmd = 'claude --resume "%s"' % tok
    proj = project_of(e)
    return cmd if proj in (CODE_ROOT_SHORT, "?") else "cd %s && %s" % (proj, cmd)


class AccountError(Exception):
    """A session recorded a login this machine has no config dir for."""


def account_of(e):
    """The login the session last ran under, else the configured default."""
    return e.get("account") or CFG["accounts"]["default"]


STOCK_CLAUDE_DIR = os.path.expanduser("~/.claude")


def config_dir_for(email):
    """The config dir for an account. Raises AccountError for a recorded login
    with no mapping rather than silently using another one."""
    email = (email or "").lower()
    if not email or email == CFG["accounts"]["default"]:
        return CFG["paths"]["claude_dir"]
    d = CFG["accounts"]["dirs"].get(email)
    if not d:
        raise AccountError("no config dir for %s; run `wl account add %s` or set accounts.default" % (email, email))
    return d


def config_env(config_dir):
    """CLAUDE_CONFIG_DIR to set for a config dir. Always set, so a worklog
    started from an alternate-account shell cannot leak that account into
    other sessions. The stock ~/.claude must be given as an EMPTY value: Claude
    Code keys its keychain login by the variable, and naming the stock path
    explicitly selects a different, logged-out entry."""
    stock = os.path.realpath(config_dir) == os.path.realpath(STOCK_CLAUDE_DIR)
    return {"CLAUDE_CONFIG_DIR": "" if stock else config_dir}


def account_state(e):
    """'default' | 'mapped' | 'unmapped' for the UI and CLI."""
    try:
        config_dir_for(account_of(e))
    except AccountError:
        return "unmapped"
    return "mapped" if e.get("account") and e["account"] != CFG["accounts"]["default"] else "default"


def launch_argv(e):
    """argv, cwd and env additions to resume this session under its own account.
    Always by uuid, so a shared name can never pick the wrong session."""
    cwd = e.get("cwd") or CODE_ROOT
    if not os.path.isdir(cwd):                     # the directory may be gone since
        cwd = CODE_ROOT if os.path.isdir(CODE_ROOT) else HOME
    env = config_env(config_dir_for(account_of(e)))
    return ["claude", "--resume", e["sid"]], cwd, env


_PR = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)$")


def short_pr(url):
    """'repo#123' inside the configured org, 'owner/repo#123' elsewhere."""
    m = _PR.match(url)
    if not m:
        return url
    owner, repo, n = m.groups()
    return "%s#%s" % (repo, n) if owner.lower() == CFG["github"]["org"].lower() else "%s/%s#%s" % (owner, repo, n)


def needs_summary(e, m):
    """No summary yet, or the session has grown by a quarter since it was written."""
    if is_tiny(e) and not m.get("label"):
        return False
    done = m.get("enriched", 0)
    if not done or not (m.get("auto_label") or m.get("label")):   # never run, or run before auto labels existed
        return True
    return e.get("n_prompts", 0) > done * 1.25 + 5


def _terms(q):
    return [t for t in (q or "").lower().split() if t]


def _snippet(text, term, width=160):
    i = text.lower().find(term)
    if i < 0:
        return ""
    a = max(0, i - width // 2)
    b = min(len(text), i + len(term) + width // 2)
    return ("…" if a else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")


def matches(e, meta, terms):
    """All terms must appear somewhere (label, names, titles, summary, prompts).
    Returns None for no match, else a snippet from the prompts (may be '')."""
    if not terms:
        return ""
    head = " ".join([meta.get("label", ""), meta.get("auto_label", ""), " ".join(meta.get("tags", [])),
                     " ".join(meta.get("auto_tags", [])), " ".join(people_of(meta)),
                     meta.get("note", ""), meta.get("summary", ""), meta.get("outcome", ""),
                     " ".join(e.get("titles", [])), e.get("ai_title", ""), e.get("summary", ""),
                     " ".join(e.get("prs", [])), " ".join(e.get("issues", [])),
                     " ".join(e.get("repos", {})), e.get("last_reply", ""), e["sid"],
                     project_of(e)]).lower()
    body = "\n".join(e.get("prompts", [])).lower()
    snippet = ""
    for t in terms:
        if t in head:
            continue
        if t in body:
            snippet = snippet or _snippet(body, t)
            continue
        return None
    return snippet


def people_of(m):
    return [p for p in m.get("people", []) if p.strip().lower() not in CFG["user"]["names"]]


def is_active(e, now=None):
    """Activity inside the configured window (UTC timestamps in the transcript)."""
    last = e.get("last", "")
    if not last:
        return False
    now = now or time.time()
    try:
        import datetime as _dt
        t = _dt.datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return False
    return now - t < CFG["catalog"]["active_hours"] * 3600


def rows(cat, meta, threads, q=""):
    """Catalog entries shaped for the API/CLI, newest activity first."""
    terms = _terms(q)
    now = time.time()
    by_title = {}
    for e in cat.values():
        t = title_of(e)
        if t:
            by_title.setdefault(t, []).append(e["sid"])
    out = []
    for e in cat.values():
        m = meta.get(e["sid"], {})
        snip = matches(e, m, terms)
        if snip is None:
            continue
        t = title_of(e)
        aliases = set(e.get("titles", [])) | {e["sid"]}
        linked = sorted({w for name in aliases for w in threads.get(name, [])})
        out.append({
            "sid": e["sid"], "title": t, "titles": e.get("titles", []),
            "ai_title": e.get("ai_title", ""), "summary": e.get("summary", ""),
            "first_prompt": e.get("first_prompt", ""), "n_prompts": e.get("n_prompts", 0),
            "project": project_of(e), "branch": e.get("branch", ""),
            "created": e.get("created", ""), "last": e.get("last", ""),
            "size": e.get("size", 0), "path": e.get("path", ""),
            "tiny": is_tiny(e), "active": is_active(e, now),
            "account": account_of(e), "account_known": bool(e.get("account")),
            "account_state": account_state(e),
            "account_dir": CFG["accounts"]["dirs"].get(account_of(e), ""), "cwd": e.get("cwd") or CODE_ROOT,
            "shared": len(by_title.get(t, [])) - 1 if t else 0,
            "label": m.get("label", ""), "auto_label": m.get("auto_label", ""),
            "tags": m.get("tags", []), "note": m.get("note", ""),
            "summary_ai": m.get("summary", ""), "outcome": m.get("outcome", ""),
            "auto_tags": m.get("auto_tags", []), "people": people_of(m),
            "enriched": m.get("enriched", 0), "stale": needs_summary(e, m),
            "prs": e.get("prs", []), "issues": e.get("issues", []),
            "repos": sorted(e.get("repos", {}), key=lambda k: -e["repos"][k]),
            "days": len(e.get("days", [])), "last_reply": e.get("last_reply", ""),
            "threads": linked,
            "resume_cmd": resume_cmd(e), "resume_uuid_cmd": resume_cmd(e, by_uuid=True),
            "snippet": snip,
        })
    out.sort(key=lambda r: r["last"], reverse=True)
    return out


def resolve(cat, key):
    """A uuid, a uuid prefix, or a session name (current or former). Returns the
    matching entries, newest first."""
    key = (key or "").strip()
    if not key:
        return []
    if UUID.match(key):
        return [cat[key]] if key in cat else []
    hits = [e for e in cat.values() if key in e.get("titles", [])]
    if not hits and key.strip('"') != key:         # a name that itself contains quotes is rare
        return resolve(cat, key.strip('"'))
    if not hits and len(key) >= 6 and all(c in "0123456789abcdef-" for c in key):
        hits = [e for sid, e in cat.items() if sid.startswith(key)]
    cur = [e for e in hits if title_of(e) == key]
    hits = cur or hits
    return sorted(hits, key=lambda e: e.get("last", ""), reverse=True)
