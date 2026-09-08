#!/usr/bin/env python3
"""herdr-quota — AI plan quotas in the Herdr tab bar, Vibe-Island style.

  5h 28% 1h6m | 7d 17% 3d22h | Fable 20% 3d22h

Default mode: `status` prints that line for a ui.tab_bar_right command entry and
caches the fetch. Optional sidebar mode: the `start` action runs a daemon that
publishes $q{i}_label / $q{i}_pct / $q{i}_reset tokens (i = 1..MAX_WINDOWS) on a
"Quota" mini-space at the bottom of the spaces list.

Commands:
  ensure  start the background monitor if not already running (event hooks use this)
  daemon  the monitor loop itself (spawned by ensure)
  once    fetch + publish a single time (manual refresh)
  stop    stop the monitor, clear tokens, close the mini-space we created
  popup   detail view for `herdr plugin pane open`
  status  one plain-text line for ui.tab_bar_right command entries (reads the daemon cache)
"""
import json
import os
import signal
import socket as sk
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers import RateLimited, load_all  # noqa: E402

PLUGIN_ID = "arnaud.quota"
SOURCE = f"plugin:{PLUGIN_ID}"
MAX_WINDOWS = 6
INTERVAL_S = 120
TTL_MS = INTERVAL_S * 5 * 1000
WS_LABEL = "Quota"

HERDR = os.environ.get("HERDR_BIN_PATH", "herdr")
STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR") or os.path.expanduser("~/.local/state/herdr-quota")
SOCKET_PATH = os.environ.get("HERDR_SOCKET_PATH", "")
PIDFILE = os.path.join(STATE_DIR, "monitor.pid")
WS_ID_FILE = os.path.join(STATE_DIR, "workspace.id")
CACHE_FILE = os.path.join(STATE_DIR, "last.json")


# ---------- data ----------

def collect():
    """[(provider_module, [window,...]), ...]; raises RateLimited if any provider is."""
    result = []
    for prov in load_all():
        try:
            wins = prov.windows()
        except RateLimited:
            raise
        except Exception:
            wins = []
        if wins:
            result.append((prov, wins))
    return result


def until(iso):
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int((dt - datetime.now(timezone.utc)).total_seconds())
    if secs <= 0:
        return "now"
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    return f"{m}m"


def tokens(collected):
    """Flatten windows into the $q tokens. Labels get a provider prefix only when >1 provider."""
    toks = {"q_icon": "".join(p.ICON for p, _ in collected)}
    multi = len(collected) > 1
    i = 0
    for prov, wins in collected:
        for w in wins:
            i += 1
            if i > MAX_WINDOWS:
                break
            label = w["label"]
            if multi:
                label = f"{prov.NAME.capitalize()} {label}" if label in ("5h", "7d") else label
            toks[f"q{i}_label"] = label
            toks[f"q{i}_pct"] = f"{w['pct']}%"
            toks[f"q{i}_reset"] = until(w.get("resets_at"))
    return toks


# ---------- herdr ----------

def herdr_cli(*args):
    return subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=10)


def list_workspaces():
    out = herdr_cli("workspace", "list")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "workspace list failed")
    return json.loads(out.stdout)["result"]["workspaces"]


def socket_request(method, params):
    req = {"id": f"plugin:{PLUGIN_ID}:{time.time_ns()}", "method": method, "params": params}
    try:
        c = sk.socket(sk.AF_UNIX, sk.SOCK_STREAM)
        c.settimeout(2)
        c.connect(SOCKET_PATH)
        c.sendall((json.dumps(req) + "\n").encode())
        try:
            c.recv(65536)
        except Exception:
            pass
        c.close()
    except Exception:
        pass


def stored_workspace_id():
    try:
        return open(WS_ID_FILE).read().strip() or None
    except OSError:
        return None


def ensure_quota_workspace(workspaces):
    ids = [w["workspace_id"] for w in workspaces]
    target = stored_workspace_id()
    if target not in ids:
        target = next((w["workspace_id"] for w in workspaces if w.get("label") == WS_LABEL), None)
    if target is None:
        out = herdr_cli("workspace", "create", "--label", WS_LABEL, "--cwd", STATE_DIR, "--no-focus")
        if out.returncode != 0:
            return None
        target = json.loads(out.stdout)["result"]["workspace"]["workspace_id"]
        with open(WS_ID_FILE, "w") as f:
            f.write(target)
    if ids and ids[-1] != target:
        socket_request("workspace.move", {"workspace_id": target, "insert_index": len(ids)})
    return target


MAX_PROPS = 16  # server-side cap per report_metadata call


def publish(workspace_id, toks, previous=None):
    """Set toks; clear only names that were set last time and are gone now. Chunked under MAX_PROPS."""
    props = dict(toks)
    for k in (previous or {}):
        if k not in toks:
            props[k] = None
    items = list(props.items())
    for i in range(0, len(items), MAX_PROPS):
        args = ["workspace", "report-metadata", workspace_id, "--source", SOURCE, "--ttl-ms", str(TTL_MS)]
        for k, v in items[i:i + MAX_PROPS]:
            args += ["--clear-token", k] if v is None else ["--token", f"{k}={v}"]
        herdr_cli(*args)


def clear(workspace_id):
    names = ["q_icon"] + [f"q{i}_{sfx}" for i in range(1, MAX_WINDOWS + 1) for sfx in ("label", "pct", "reset")]
    for i in range(0, len(names), MAX_PROPS):
        args = ["workspace", "report-metadata", workspace_id, "--source", SOURCE]
        for n in names[i:i + MAX_PROPS]:
            args += ["--clear-token", n]
        herdr_cli(*args)


def running_pid():
    try:
        pid = int(open(PIDFILE).read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


# ---------- commands ----------

def tick(state):
    workspaces = list_workspaces()
    rate_limited = False
    try:
        collected = collect()
        if collected:
            state["toks"] = tokens(collected)
            with open(CACHE_FILE, "w") as f:
                json.dump([(p.NAME, w) for p, w in collected], f)
    except RateLimited:
        rate_limited = True
    if state.get("toks"):
        target = ensure_quota_workspace(workspaces)
        if target:
            if target != state.get("target"):
                for w in workspaces:
                    if w["workspace_id"] != target:
                        clear(w["workspace_id"])
                state["target"] = target
            publish(target, state["toks"], state.get("published"))
            state["published"] = state["toks"]
    return rate_limited


def cmd_once():
    os.makedirs(STATE_DIR, exist_ok=True)
    tick({})


def cmd_ensure():
    if running_pid():
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "daemon"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def cmd_daemon():
    os.makedirs(STATE_DIR, exist_ok=True)
    other = running_pid()
    if other and other != os.getpid():
        return
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    failures = 0
    state = {}
    while True:
        if SOCKET_PATH and not os.path.exists(SOCKET_PATH):
            break
        try:
            rate_limited = tick(state)
            failures = 0
        except Exception:
            failures += 1
            if failures >= 3:
                break
            time.sleep(10)
            continue
        time.sleep(INTERVAL_S * 3 if rate_limited else INTERVAL_S)
    try:
        os.remove(PIDFILE)
    except OSError:
        pass


def cmd_stop():
    pid = running_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    try:
        workspaces = list_workspaces()
        for w in workspaces:
            clear(w["workspace_id"])
        mine = stored_workspace_id()
        if mine and any(w["workspace_id"] == mine for w in workspaces):
            herdr_cli("workspace", "close", mine)
            os.remove(WS_ID_FILE)
    except Exception:
        pass
    try:
        os.remove(PIDFILE)
    except OSError:
        pass


def cmd_status():
    """`5h 33% 48m | 7d 18% 3d21h | Fable 22% 3d21h` from the cache the daemon writes (fetches if stale)."""
    try:
        stale = time.time() - os.path.getmtime(CACHE_FILE) > INTERVAL_S * 3
        with open(CACHE_FILE) as f:
            cached = json.load(f)
    except Exception:
        cached, stale = None, True
    if stale:
        try:
            fresh = [(p.NAME, w) for p, w in collect()]
            if fresh:
                cached = fresh
                os.makedirs(STATE_DIR, exist_ok=True)
                with open(CACHE_FILE, "w") as f:
                    json.dump(cached, f)
        except RateLimited:
            pass
    if not cached:
        print("quota: n/a")
        return
    parts = []
    for name, wins in cached:
        for w in wins:
            label = w["label"]
            if len(cached) > 1 and label in ("5h", "7d"):
                label = f"{name.capitalize()} {label}"
            r = until(w.get("resets_at"))
            parts.append(f"{label} {w['pct']}%" + (f" {r}" if r else ""))
    print(" | ".join(parts))


def bar(pct, width=30):
    filled = min(width, round(width * pct / 100))
    color = "\033[31m" if pct >= 90 else "\033[33m" if pct >= 70 else "\033[32m"
    return f"{color}{'█' * filled}\033[2m{'░' * (width - filled)}\033[0m"


def local_time(iso):
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%a %H:%M")
    except ValueError:
        return iso


def cmd_popup():
    while True:
        try:
            collected = collect()
        except RateLimited:
            collected = []
        sys.stdout.write("\033[2J\033[H")
        print("\033[1m AI quotas\033[0m")
        print()
        if not collected:
            print("  No data. Provider not logged in on this machine, or rate-limited.")
        for prov, wins in collected:
            print(f"  \033[1m{prov.ICON} {prov.NAME.capitalize()}\033[0m")
            for w in wins:
                r = until(w.get("resets_at"))
                print(f"    {w['label']:<8} {bar(w['pct'])}  {w['pct']:>3}%   "
                      f"resets in {r or '?'} ({local_time(w.get('resets_at'))})")
            print()
        print("\033[2m refreshes every 30s — close the popup to exit\033[0m")
        sys.stdout.flush()
        time.sleep(30)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ensure"
    {"ensure": cmd_ensure, "daemon": cmd_daemon, "once": cmd_once, "status": cmd_status,
     "stop": cmd_stop, "popup": cmd_popup}.get(cmd, cmd_ensure)()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
