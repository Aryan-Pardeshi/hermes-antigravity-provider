# hermes-antigravity-provider

Run Google Antigravity models inside [Hermes Agent](https://github.com/NousResearch/hermes-agent) through the official `agy` CLI.

Gives Hermes access to Claude Sonnet 4.6, Claude Opus 4.6, the Gemini 3.x line, and GPT-OSS 120B, with streaming, multi-turn conversations, and function calling.

## What this does and does not do

This package **shells out to the `agy` binary Google ships**. That is the whole design.

It does **not**:

- read, copy, or refresh your Antigravity OAuth token — `agy` owns its own credential, and `agy auth login` is the only login path
- reimplement Google's OAuth flow or borrow Antigravity's OAuth client id
- send spoofed device fingerprints, IDE identifiers, or user-agent strings
- rotate between multiple Google accounts, or pool quota across accounts
- patch any file inside Hermes

Every request is made by the official client with the identity it normally sends. If you are looking for multi-account rotation or fingerprint spoofing to stretch the free tier, this is the wrong repository, and you should know that Google enforces against that at the backend layer — a ban can extend to the whole Google account, including Gmail and Drive.

**One thing you should still decide for yourself:** Antigravity is a consumer product with a free tier, and this automates it. Whether that fits Google's terms for your account is your call, not something this README can settle for you.

## Requirements

| Requirement | Notes |
| --- | --- |
| `agy` CLI | Google Antigravity CLI, v1.1.12 or newer. Must be on `PATH`, or point `HERMES_ANTIGRAVITY_COMMAND` at it. |
| A logged-in `agy` | Run `agy auth login` once. |
| Python | 3.10 or newer. |
| Hermes Agent | Any version with user model-provider plugin discovery. |

## Setup

### 1. Confirm `agy` works

```bash
agy models
```

You should see a list like `gemini-3.6-flash-low`, `claude-sonnet-4-6`, and so on. If it errors, run `agy auth login` first. Nothing below will work until this does.

### 2. Install this package

```bash
pip install git+https://github.com/Aryan-Pardeshi/hermes-antigravity-provider.git
```

Or from a clone:

```bash
git clone https://github.com/Aryan-Pardeshi/hermes-antigravity-provider.git
cd hermes-antigravity-provider
pip install -e .
```

> **On the command name.** All commands below use `python -m hermes_antigravity`,
> which always works. `pip` also installs a `hermes-antigravity` console script,
> but on many systems its directory is not on `PATH` — if `hermes-antigravity`
> is not found, that is why, and the module form is the fix.

### 3. Install the two `agy` agents

```bash
python -m hermes_antigravity setup
```

This installs an `agy` plugin defining two agents. It is not optional — it is what makes the provider affordable, and the numbers are in [Cost](#cost) below.

| Agent | Tools | Used for |
| --- | --- | --- |
| `hermes-passthrough` | none | every normal request |
| `hermes-reader` | file read only | prompts too large for argv |

To remove them later: `agy plugin uninstall hermes-antigravity`.

### 4. Install the Hermes provider plugin

Hermes discovers user plugins from `$HERMES_HOME/plugins/model-providers/`, with no changes to the Hermes repo.

**Find your Hermes home first — it is usually not `~/.hermes`.** On Windows it defaults to `%LOCALAPPDATA%\hermes`:

```bash
echo $HERMES_HOME
```

Linux and macOS:

```bash
HH="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HH/plugins/model-providers"
cp -r plugins/model-providers/antigravity "$HH/plugins/model-providers/"
```

Windows PowerShell:

```powershell
$HH = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { "$env:LOCALAPPDATA\hermes" }
New-Item -ItemType Directory -Force "$HH\plugins\model-providers"
Copy-Item -Recurse plugins\model-providers\antigravity "$HH\plugins\model-providers\"
```

### 5. Enable the plugin

Hermes discovers user plugins but does not enable them automatically:

```bash
hermes plugins enable antigravity
```

Hermes will ask whether to grant tool-override permission. **Answer no.** This provider only declares an inference backend and never needs to intercept built-in tools.

Confirm:

```bash
hermes plugins list
```

The `antigravity` row should read `enabled`.

### 6. Credential and bridge: handled for you

Two things used to be manual here and no longer are.

**The credential.** Hermes requires one for every provider. The bridge is
unauthenticated loopback, so the plugin sets `HERMES_ANTIGRAVITY_API_KEY` to a
placeholder on import. Your real Antigravity credential stays inside `agy` and
is never read by Hermes or by this package. Set the variable yourself only if
you want a different value.

**The bridge.** The plugin checks whether anything is serving its base URL and,
if not, starts one detached so it outlives the Hermes process. Set
`HERMES_ANTIGRAVITY_NO_AUTOSTART=1` to turn that off and manage it yourself.

The bridge is started with `CREATE_NO_WINDOW`, and with `pythonw.exe` when it
sits next to the chosen interpreter, so nothing appears on screen. Use
`python -m hermes_antigravity serve` in a terminal when you want to watch it.

Autostart needs an interpreter that can import `hermes_antigravity`. Hermes runs
in its own venv, which usually cannot, so the plugin tries `sys.executable`
first and then each `python` on `PATH`. If none of them work, point
`HERMES_ANTIGRAVITY_PYTHON` at the interpreter you installed the package into.

### 7. Check everything

```bash
python -m hermes_antigravity doctor
```

Expected:

```
[ok]   agy binary: /path/to/agy
[ok]   agy login: 11 models available
[ok]   agent hermes-passthrough
[ok]   agent hermes-reader
[ok]   hermes CLI: /path/to/hermes
```

Any `[fail]` line tells you the command to run.

### 8. Start the bridge

```bash
python -m hermes_antigravity serve
```

It listens on `http://127.0.0.1:8787/v1`, loopback only.

You normally do not need this — the plugin starts the bridge on demand. Run it
by hand when you want to watch its output, or when autostart is disabled.

To use a different port:

```bash
python -m hermes_antigravity serve --port 9000
```

and set `HERMES_ANTIGRAVITY_BASE_URL=http://127.0.0.1:9000/v1` before starting Hermes.

### 9. Use it

```bash
hermes -z "Reply with exactly the word PONG." --provider antigravity -m gemini-3.6-flash-low
```

That is the end-to-end check: it should print `PONG`. Then pick a model for
interactive use:

```bash
hermes model
```

Verify the bridge on its own at any point:

```bash
curl http://127.0.0.1:8787/v1/models
```

## Why a bridge instead of a native provider

Hermes routes inference over HTTP. It does have a subprocess provider path — `auth_type="external_process"`, used by `copilot-acp` — but that path speaks ACP over stdio, which is a different protocol from `agy`'s print mode. Until Hermes exposes a generic way for a plugin to declare its own local command, a loopback OpenAI-compatible server is the honest way to connect the two.

[NousResearch/hermes-agent#84057](https://github.com/NousResearch/hermes-agent/pull/84057) is a step toward that: it generalises the command resolution behind `external_process` so it is no longer hardcoded to Copilot.

## Cost

`agy` normally injects its own system prompt plus roughly fifty tool definitions into every call. Measured on Windows 11 with `gemini-3.6-flash-low`:

| Request | Agent | Input tokens |
| --- | --- | --- |
| `Say exactly: PONG` | default | 23,684 |
| `Say exactly: PONG` | `hermes-passthrough` | 5,398 |
| Weather question with one tool declared | default | 23,357 |
| Weather question with one tool declared | `hermes-passthrough` | 5,805 |

That is why step 3 exists. There is still a floor of roughly 5k input tokens per request that `agy` adds and this package cannot remove.

## How it works

### Function calling

`agy` has no flag for accepting a tool specification. This package renders the OpenAI `tools` array into the prompt, asks for a JSON reply, and parses the result back into OpenAI `tool_calls` with `finish_reason: "tool_calls"`.

`agy` does have a `--json-schema` flag that enforces output shape and returns a parsed `structured_output`, which looks like the obvious mechanism. It is not used, for two measured reasons: passing it makes `agy` ignore `--agent` and fall back to its default agent, which costs about 23k input tokens per request instead of 5.6k, and that default agent carries its own toolset — in testing it answered a `get_weather` request by calling its own built-in `search_web` instead. Under the no-tool agent those tools do not exist, so the model answers with the JSON it was asked for.

### Long prompts

`agy` takes its prompt as a single argv entry, and every OS caps how long one argument can be:

- **Linux:** `MAX_ARG_STRLEN` is 32 pages, 131,072 bytes, **per string**. The ~2 MB `ARG_MAX` figure is the total for argv plus environment, so a long prompt trips the per-string cap first with `E2BIG`.
- **Windows:** `CreateProcess` caps the whole command line at 32,767 characters. Measured on Windows 11: a single argument of 32,700 bytes is accepted, 32,750 is rejected.

Environment variables are not a way around this — on Linux `execve(2)` applies the same per-string cap to environment entries, and they share the same budget.

So prompts over the limit are written to a file in a fresh directory, that directory is handed to `agy` with `--add-dir`, and argv carries a short pointer instead. Verified with a 63,106-byte prompt, roughly twice the Windows ceiling.

The prompt file leads with its instructions. Without that header a 60 KB prompt came back as echoed source text during testing.

### Multi-turn

The first request sends the full conversation. `agy` returns a `conversation_id`, which the bridge caches and replays with `--conversation` on later turns, so history is not resent.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `HERMES_ANTIGRAVITY_API_KEY` | Placeholder credential; any non-empty value | set to `local-bridge` by the plugin |
| `HERMES_ANTIGRAVITY_NO_AUTOSTART` | Any non-empty value disables bridge autostart | unset |
| `HERMES_ANTIGRAVITY_PYTHON` | Interpreter used to start the bridge | `sys.executable`, then `PATH` |
| `HERMES_ANTIGRAVITY_COMMAND` | Path to the `agy` binary | found on `PATH` |
| `AGY_CLI_PATH` | Alternate path variable, checked second | — |
| `HERMES_ANTIGRAVITY_BASE_URL` | Where the provider expects the bridge | `http://127.0.0.1:8787/v1` |

## Known limitations

- **Sampling parameters are ignored.** `agy` exposes no `--temperature`, `--top-p`, `--max-tokens`, or stop sequences. Hermes may send them; they have no effect. `reasoning_effort` of `low`, `medium`, or `high` does map onto `--effort`.
- **Streaming is chunked, not incremental.** `agy` streams text deltas, but a tool-calling turn is only decidable once the reply is complete, so the bridge buffers and then emits SSE chunks.
- **Latency.** One process spawn per request, plus `agy`'s own startup. Expect seconds, not milliseconds.
- **Free-tier limits.** Rate limits and 429s are between you and Google. This package does not retry around them, and deliberately has no mechanism to spread load across accounts.
- **Images are dropped** with a text placeholder. `agy` print mode is text-only.
- **The ~5k token floor** per request cannot be removed from outside `agy`.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `No usable credentials found for provider 'antigravity'` | The plugin sets this automatically; seeing it means an older copy of the plugin is installed. Re-copy step 4, or set `HERMES_ANTIGRAVITY_API_KEY` to any non-empty value. |
| Hermes does not list the provider | The plugin is discovered but not enabled. Run `hermes plugins enable antigravity`, then check `hermes plugins list`. |
| Plugin not discovered at all | It went into the wrong directory. Hermes home is often `%LOCALAPPDATA%\hermes` on Windows, not `~/.hermes` — check `echo $HERMES_HOME` and redo step 4. |
| `hermes-antigravity: command not found` | The console script directory is not on `PATH`. Use `python -m hermes_antigravity` instead. |
| Connection refused from Hermes | Autostart could not find an interpreter with the package. Set `HERMES_ANTIGRAVITY_PYTHON`, or start the bridge yourself with `python -m hermes_antigravity serve`. |
| `'agy models' returned no models` | `agy` is not logged in. Run `agy auth login`. |
| Terminal windows appear when Hermes starts | Fixed in 0.1.1. An older plugin copy used `DETACHED_PROCESS`, which still lets Windows open a console for `python.exe`. Re-copy step 4. |

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

The test suite covers prompt sizing, the argv/file switch, and every translation path. It does not call `agy`, so it costs no quota and needs no login.

## License

MIT
