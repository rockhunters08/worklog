"""Several Claude logins on one machine.

Claude Code keeps one login per config directory (the keychain entry is keyed
by it), so a second account needs a second CLAUDE_CONFIG_DIR. To keep every
account on the same transcripts, settings, skills and memory, an account dir
is a directory of symlinks back into the default one: only the login differs.

    ./wl account add you+work@example.com work     # make the dir, print the login command
    ./wl account ls                                # every account and whether it is logged in

Recorded in worklog.toml under [accounts.dirs] as email -> dir; a session then
resumes under the account it last ran with (catalog.account_of).
"""
import json
import os
import subprocess

from config import CFG, PATH as CONFIG_PATH

DEFAULT_DIR = os.path.expanduser("~/.claude")
ACCOUNTS_ROOT = os.path.expanduser("~/.claude-accounts")
# everything an account dir shares with the default one; anything Claude Code
# creates later lands in the account dir itself, which is fine
SHARED = ["projects", "settings.json", "settings.local.json", "CLAUDE.md", "skills", "plugins",
          "commands", "agents", "memory", "history.jsonl", "plans", "scripts", "venvs",
          "file-history", "todos", "shell-snapshots", "statsig", "cache", "image-cache", "paste-cache"]


def make_dir(alias):
    """Create ~/.claude-accounts/<alias> as a symlink farm over ~/.claude."""
    d = os.path.join(ACCOUNTS_ROOT, alias)
    os.makedirs(d, exist_ok=True)
    for name in SHARED:
        src, dst = os.path.join(DEFAULT_DIR, name), os.path.join(d, name)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
    # ~/.claude.json (mcp servers, per-project state) lives beside the dir, not in it
    src, dst = os.path.expanduser("~/.claude.json"), os.path.join(d, ".claude.json")
    if os.path.exists(src) and not os.path.lexists(dst):
        os.symlink(src, dst)
    return d


def status(config_dir=""):
    """(logged_in, email) for a config dir ('' = the default one)."""
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    else:
        env.pop("CLAUDE_CONFIG_DIR", None)
    try:
        r = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, env=env, timeout=30)
        d = json.loads(r.stdout)
        return bool(d.get("loggedIn")), (d.get("email") or "").lower()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False, ""


def login_cmd(email, config_dir):
    return "CLAUDE_CONFIG_DIR=%s claude auth login --email %s" % (config_dir, email)


def record(email, config_dir):
    """Append the mapping to worklog.toml without disturbing the rest of it."""
    email = email.lower()
    text = open(CONFIG_PATH).read() if os.path.exists(CONFIG_PATH) else ""
    line = '"%s" = "%s"\n' % (email, config_dir.replace(os.path.expanduser("~"), "~", 1))
    if "[accounts.dirs]" in text:
        head, tail = text.split("[accounts.dirs]", 1)
        text = head + "[accounts.dirs]\n" + line + tail.lstrip("\n")
    else:
        text = text.rstrip("\n") + "\n\n[accounts.dirs]\n" + line
    with open(CONFIG_PATH, "w") as f:
        f.write(text)


def listing():
    out = []
    ok, email = status("")
    out.append({"dir": DEFAULT_DIR, "email": email or CFG["accounts"]["default"], "logged_in": ok, "default": True})
    for em, d in sorted(CFG["accounts"]["dirs"].items()):
        ok, actual = status(d)
        out.append({"dir": d, "email": em, "logged_in": ok and (not actual or actual == em),
                    "actual": actual, "default": False})
    return out
