"""Claude plan quotas, from the OAuth usage endpoint Claude Code's /status reads.

Uses the `limits` array: session (5h), weekly_all (7d), and each weekly_scoped
entry labeled by its model display name (e.g. Fable).
"""
import json
import os
import subprocess
import urllib.error
import urllib.request

from . import RateLimited

NAME = "claude"
ICON = "✳"
URL = "https://api.anthropic.com/api/oauth/usage"
LABELS = {"session": "5h", "weekly_all": "7d"}


def _token():
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return json.loads(out.stdout)["claudeAiOauth"]["accessToken"]
    except Exception:
        pass
    try:
        with open(os.path.expanduser("~/.claude/.credentials.json"), encoding="utf-8") as f:
            return json.load(f)["claudeAiOauth"]["accessToken"]
    except Exception:
        return None


def windows():
    token = _token()
    if not token:
        return []
    req = urllib.request.Request(URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited()
        raise
    out = []
    for lim in data.get("limits") or []:
        kind = lim.get("kind")
        if kind in LABELS:
            label = LABELS[kind]
        elif kind == "weekly_scoped":
            label = (((lim.get("scope") or {}).get("model") or {}).get("display_name")
                     or ((lim.get("scope") or {}).get("surface")) or "scoped")
        else:
            continue
        pct = lim.get("percent")
        if pct is None:
            continue
        out.append({"label": label, "pct": round(pct), "resets_at": lim.get("resets_at")})
    if not out:  # older shape without `limits`
        for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
            b = data.get(key) or {}
            if b.get("utilization") is not None:
                out.append({"label": label, "pct": round(b["utilization"]), "resets_at": b.get("resets_at")})
    return out
