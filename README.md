# herdr-quota

AI plan quotas in the [Herdr](https://herdr.dev) spaces sidebar, Vibe-Island style:

```
✳ · 5h · 28% · 1h6m · 7d · 17% · 3d22h · Fable · 20% · 3d22h
```

Provider-pluggable. Ships with **Claude** (the same numbers Claude Code's `/status` shows:
5h session, 7d all-models, and every per-model weekly cap such as Fable, read from the
`limits` array of Anthropic's OAuth usage endpoint using the credentials Claude Code
already stores on the machine). No extra login.

## Install

```bash
git clone https://github.com/arnaudrinquin/herdr-quota ~/projects/herdr-quota
herdr plugin link ~/projects/herdr-quota
```

Add the row to `~/.config/herdr/config.toml` (Herdr ≥ 0.8.2):

```toml
[ui.sidebar.spaces]
rows = [
  ["state_icon", "workspace"],
  ["branch", "git_status"],
  [
    { token = "$q_icon", fg = "#f38ba8" },
    { token = "$q1_label", bold = true },
    { token = "$q1_pct", fg = "#a6e3a1", bold = true },
    { token = "$q1_reset", dim = true },
    { token = "$q2_label", bold = true },
    { token = "$q2_pct", fg = "#a6e3a1", bold = true },
    { token = "$q2_reset", dim = true },
    { token = "$q3_label", bold = true },
    { token = "$q3_pct", fg = "#a6e3a1", bold = true },
    { token = "$q3_reset", dim = true },
  ],
]
```

On Herdr ≥ 0.9.0 the `%` tokens can change color with usage (`rules` are rejected by 0.8.x):

```toml
{ token = "$q1_pct", fg = "#a6e3a1", bold = true, rules = [{ equals = "100%", fg = "#f38ba8" }, { starts_with = "9", fg = "#f38ba8" }, { starts_with = "8", fg = "#fab387" }, { starts_with = "7", fg = "#f9e2af" }] },
```

(`starts_with` rather than `gt` because the value carries a `%`, which blocks numeric matching.)

Then:

```bash
herdr server reload-config
herdr plugin action invoke start --plugin arnaud.quota
```

The monitor keeps a mini-space labeled **Quota** pinned on top of the spaces list and
publishes the tokens only there, so the line renders once. It auto-starts on
workspace/pane creation events and exits when the Herdr server goes away.

## Popup

```toml
[[keys.command]]
key = "prefix+u"
type = "shell"
command = '"$HERDR_BIN_PATH" plugin pane open --plugin arnaud.quota --entrypoint usage'
```

## Tokens

| Token | Example |
|---|---|
| `$q_icon` | `✳` (one glyph per active provider) |
| `$q{i}_label` | `5h`, `7d`, `Fable` — prefixed with the provider name when several providers are active |
| `$q{i}_pct` | `28%` |
| `$q{i}_reset` | `1h6m`, `3d22h` — time until the window resets |

`i` runs 1..6 across all providers, in provider order. Missing windows vanish from the row.

## Adding a provider

Drop `providers/<name>.py` exposing `NAME`, `ICON` and `windows()` returning
`[{"label": str, "pct": int, "resets_at": iso8601 | None}, ...]` (empty list when not
configured on this machine; raise `providers.RateLimited` on 429), and add it to
`load_all()` in `providers/__init__.py`.

## Commands

| | |
|---|---|
| `herdr plugin action invoke start --plugin arnaud.quota` | start the monitor |
| `herdr plugin action invoke refresh --plugin arnaud.quota` | fetch + publish once |
| `herdr plugin action invoke stop --plugin arnaud.quota` | stop, clear tokens, close the mini-space |
| `herdr plugin pane open --plugin arnaud.quota --entrypoint usage` | detail popup |

Requirements: Herdr ≥ 0.8.2, `python3` (stdlib only), macOS or Linux.
