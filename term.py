"""Terminals for catalog sessions: open one in the user's terminal app, or
attach one to the web page over a WebSocket.

A session's terminal is a tmux session named wl-<uuid prefix> when tmux is
installed and enabled, so the page, Terminal.app and a plain `tmux attach`
all reach the same running `claude`, and closing any of them only detaches.
Without tmux the page gets a bare pty and closing the tab ends the process.

The WebSocket server is the few dozen lines of RFC 6455 this needs (text
frames carry JSON control messages, binary frames carry terminal bytes), so
the app stays standard-library only.
"""
import base64
import fcntl
import hashlib
import json
import os
import pty
import select
import shlex
import shutil
import signal
import struct
import subprocess
import termios

from config import CFG

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def tmux():
    return shutil.which("tmux") if CFG["launch"]["tmux"] else None


def tmux_name(sid):
    return "wl-" + sid[:8]


def shell_cmd(argv, cwd, env):
    """One line a shell can run: cd, env, command."""
    parts = ["cd %s" % shlex.quote(cwd)]
    exports = " ".join("%s=%s" % (k, shlex.quote(v)) for k, v in env.items())
    parts.append(((exports + " ") if exports else "") + " ".join(shlex.quote(a) for a in argv))
    return " && ".join(parts)


def wrapped_argv(sid, argv, cwd, env):
    """What actually gets executed: the tmux session (attach if it already
    exists, else create it running the command) or the bare command."""
    t = tmux()
    if not t:
        return argv, cwd, env
    cmd = [t, "new-session", "-A", "-s", tmux_name(sid), "-c", cwd]
    for k, v in env.items():
        cmd += ["-e", "%s=%s" % (k, v)]
    return cmd + argv, cwd, {}


def tmux_alive(sid):
    t = tmux()
    if not t:
        return False
    return subprocess.run([t, "has-session", "-t", tmux_name(sid)], capture_output=True).returncode == 0


# ------------------------------------------------------------ terminal apps

def _applescript(*lines):
    cmd = ["osascript"]
    for l in lines:
        cmd += ["-e", l]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "osascript failed")


def _as_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def open_in_terminal(sid, argv, cwd, env):
    """Open the session in the configured terminal application. Returns the
    shell line it ran, for the UI to show."""
    argv, cwd, env = wrapped_argv(sid, argv, cwd, env)
    line = shell_cmd(argv, cwd, env)
    how = CFG["launch"]["terminal"]
    if how == "Terminal":
        _applescript('tell application "Terminal"', "activate",
                     "do script " + _as_str(line), "end tell")
    elif how == "iTerm":
        _applescript('tell application "iTerm"', "activate",
                     "set w to (create window with default profile)",
                     "tell current session of w to write text " + _as_str(line), "end tell")
    elif how == "command":
        tmpl = CFG["launch"]["command"]
        if not tmpl:
            raise RuntimeError("launch.terminal is 'command' but launch.command is empty")
        subprocess.Popen(tmpl.format(cmd=shlex.quote(line), cwd=shlex.quote(cwd)), shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        raise RuntimeError("unknown launch.terminal %r" % how)
    return line


# ------------------------------------------------------------ websocket pty

def handshake(handler):
    """Upgrade an http.server request to a WebSocket. Returns the socket."""
    key = handler.headers.get("Sec-WebSocket-Key")
    if handler.headers.get("Upgrade", "").lower() != "websocket" or not key:
        raise ValueError("not a websocket request")
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept)
    handler.end_headers()
    handler.wfile.flush()
    return handler.connection


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def read_frame(sock):
    """(opcode, payload) of one client frame; clients always mask."""
    b1, b2 = _recv_exact(sock, 2)
    opcode = b1 & 0x0F
    masked = b2 & 0x80
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    mask = _recv_exact(sock, 4) if masked else b""
    data = _recv_exact(sock, n)
    if masked:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return opcode, data


def send_frame(sock, data, opcode=2):
    head = bytes([0x80 | opcode])
    n = len(data)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    sock.sendall(head + data)


def set_winsize(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def serve_pty(sock, sid, argv, cwd, env, cols=120, rows=36):
    """Run the session on a pty and pump bytes both ways until either side
    closes. Control messages from the page are JSON text frames:
    {"resize":[cols,rows]}."""
    argv, cwd, env = wrapped_argv(sid, argv, cwd, env)
    child_env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor", **env)
    child_env.pop("CLAUDECODE", None)
    pid, fd = pty.fork()
    if pid == 0:                                   # child
        try:
            os.chdir(cwd)
            os.execvpe(argv[0], argv, child_env)
        except OSError as ex:
            os.write(1, ("worklog: cannot start %s: %s\r\n" % (argv[0], ex)).encode())
        finally:
            os._exit(127)
    set_winsize(fd, cols, rows)
    sock.setblocking(True)
    try:
        while True:
            r, _, _ = select.select([fd, sock], [], [], 30)
            if fd in r:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                send_frame(sock, data, 2)
            if sock in r:
                opcode, data = read_frame(sock)
                if opcode == 8:
                    break
                if opcode == 9:
                    send_frame(sock, data, 10)
                elif opcode == 2:
                    os.write(fd, data)
                elif opcode == 1:
                    try:
                        msg = json.loads(data.decode())
                    except ValueError:
                        continue
                    if "resize" in msg:
                        c, rw = msg["resize"]
                        set_winsize(fd, max(20, int(c)), max(5, int(rw)))
                        os.kill(pid, signal.SIGWINCH)
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            send_frame(sock, b"", 8)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
        # with tmux the child is only the attaching client: killing it detaches
        # and the session lives on; without tmux this ends the conversation
        try:
            os.kill(pid, signal.SIGHUP)
            os.waitpid(pid, 0)
        except (OSError, ChildProcessError):
            pass
