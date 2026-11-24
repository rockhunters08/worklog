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
import shlex
import subprocess

from config import CFG, PATH as CONFIG_PATH

DEFAULT_DIR = CFG["paths"]["claude_dir"]
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
    # the shared transcript dir must exist before it is linked, or Claude makes
    # a private one inside the account dir that the catalog never scans
    os.makedirs(os.path.join(DEFAULT_DIR, "projects"), exist_ok=True)
    for name in SHARED:
        src, dst = os.path.join(DEFAULT_DIR, name), os.path.join(d, name)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
    src, dst = state_file(DEFAULT_DIR), os.path.join(d, ".claude.json")
    if os.path.exists(src) and not os.path.lexists(dst):
        os.symlink(src, dst)
    return d


def state_file(config_dir):
    """Claude Code's .claude.json (mcp servers, per-project state): beside the
    stock ~/.claude, inside any other config dir."""
    if os.path.realpath(config_dir) == os.path.realpath(os.path.expanduser("~/.claude")):
        return os.path.expanduser("~/.claude.json")
    return os.path.join(config_dir, ".claude.json")


def status(config_dir=""):
    """(logged_in, email) for a config dir ('' = the configured default one)."""
    import catalog
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.update(catalog.config_env(config_dir or DEFAULT_DIR))
    try:
        r = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, env=env, timeout=30)
        d = json.loads(r.stdout)
        return bool(d.get("loggedIn")), (d.get("email") or "").lower()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False, ""


def login_cmd(email, config_dir):
    import catalog
    d = catalog.config_env(config_dir)["CLAUDE_CONFIG_DIR"]
    return "CLAUDE_CONFIG_DIR=%s claude auth login --email %s" % (shlex.quote(d), shlex.quote(email))


def record(email, config_dir):
    """Write the mapping into worklog.toml, replacing an existing line for the
    same email so repeated adds never produce a duplicate TOML key. Refuses a
    dir that another email already uses, since two logins in one dir would
    overwrite each other's credentials."""
    email = email.lower()
    short = config_dir.replace(os.path.expanduser("~"), "~", 1)
    for other, d in CFG["accounts"]["dirs"].items():
        if other != email and os.path.realpath(d) == os.path.realpath(config_dir):
            raise ValueError("%s is already the config dir for %s; pick another alias" % (short, other))
    if email == CFG["accounts"]["default"]:
        raise ValueError("%s is the default login (accounts.default); it needs no dir" % email)
    text = open(CONFIG_PATH).read() if os.path.exists(CONFIG_PATH) else ""
    line = '"%s" = "%s"' % (email, short)
    lines = [l for l in text.split("\n") if not l.strip().startswith('"%s"' % email)]
    text = "\n".join(lines)
    if "[accounts.dirs]" in text:
        head, tail = text.split("[accounts.dirs]", 1)
        text = head + "[accounts.dirs]\n" + line + "\n" + tail.lstrip("\n")
    else:
        text = text.rstrip("\n") + "\n\n[accounts.dirs]\n" + line + "\n"
    with open(CONFIG_PATH, "w") as f:
        f.write(text)
    CFG["accounts"]["dirs"][email] = config_dir


def listing():
    out = []
    ok, email = status("")
    out.append({"dir": DEFAULT_DIR, "email": email or CFG["accounts"]["default"], "logged_in": ok, "default": True})
    for em, d in sorted(CFG["accounts"]["dirs"].items()):
        ok, actual = status(d)
        out.append({"dir": d, "email": em, "logged_in": ok and (not actual or actual == em),
                    "actual": actual, "default": False})
    return out
