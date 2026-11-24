"""Configuration: defaults overridden by worklog.toml next to this file (or the
file named by WL_CONFIG). Every value that ties the tool to one person, team
or GitHub org lives here; the code reads CFG and never hard-codes them."""
import copy
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
PATH = os.environ.get("WL_CONFIG", os.path.join(HERE, "worklog.toml"))

DEFAULTS = {
    "paths": {
        # the default Claude Code config dir (its login is accounts.default)
        "claude_dir": "~/.claude",
        # where Claude Code keeps transcripts; one directory per project
        "projects": "~/.claude/projects",
        # sessions started under here are resumed without a `cd` prefix, and
        # the repo a session edited is the first path segment below it
        "code_root": "~/code",
        # the append-only event log; relative paths are taken from this directory
        "log": "worklog.jsonl",
        # the scanned-transcript cache, rebuilt on demand
        "cache": "sessions-cache.json",
    },
    "server": {"port": 8787},
    "launch": {
        # how "open in terminal" starts a session: "Terminal" (macOS Terminal.app),
        # "iTerm", or "command" to run `command` with {cmd} and {cwd} substituted
        "terminal": "Terminal",
        "command": "",
        # keep sessions inside tmux (when installed) so a closed window or tab
        # detaches instead of ending the conversation
        "tmux": True,
    },
    "accounts": {
        # the login in the default config dir (~/.claude); shown when a session
        # has no account recorded
        "default": "",
        # email -> CLAUDE_CONFIG_DIR for every other login, see `wl account add`
        "dirs": {},
    },
    "catalog": {
        # a session with activity inside this window shows as active
        "active_hours": 24,
    },
    "user": {
        # names and handles that should never appear in a session's people
        # list (yours, in every spelling the transcripts use)
        "names": [],
    },
    "github": {
        # PR links from this org are shown as `repo#123`; others as `owner/repo#123`
        "org": "",
    },
    "tickets": {
        # regexes for ticket ids worth pulling out of prompts
        "patterns": [r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b", r"\b[a-z][\w.-]{1,40}#\d{1,6}\b"],
    },
    "enrich": {
        # when true the server summarises new or grown sessions by itself
        "auto": False,
        "model": "haiku",
        "workers": 8,
        "max_budget_usd": 0.5,
        # the auto-tag vocabulary the summariser must pick from (1-4 per session)
        "tags": ["backend", "frontend", "mobile", "infra", "data", "security", "privacy",
                 "incident", "costs", "docs", "research", "tooling", "people", "support"],
    },
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=PATH):
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(path):
        try:
            import tomllib
        except ImportError:
            sys.exit("worklog: reading %s needs Python 3.11+ (tomllib)" % path)
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
    p = cfg["paths"]
    p["claude_dir"] = os.path.expanduser(p["claude_dir"]).rstrip("/")
    p["projects"] = os.path.expanduser(p["projects"])
    p["code_root"] = os.path.expanduser(p["code_root"]).rstrip("/")
    p["log"] = os.environ.get("WL_LOG") or os.path.join(HERE, os.path.expanduser(p["log"]))
    p["cache"] = os.path.join(HERE, os.path.expanduser(p["cache"]))
    cfg["user"]["names"] = {n.strip().lower() for n in cfg["user"]["names"] if n.strip()}
    cfg["accounts"]["dirs"] = {k.lower(): os.path.expanduser(v) for k, v in cfg["accounts"]["dirs"].items()}
    cfg["accounts"]["default"] = cfg["accounts"]["default"].lower()
    return cfg


CFG = load()
