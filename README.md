# hermes-antigravity-provider

Run Google Antigravity models inside [Hermes Agent](https://github.com/NousResearch/hermes-agent) through the official `agy` CLI.

Gives Hermes access to Claude Sonnet 4.6, Claude Opus 4.6, the Gemini 3.x line, and GPT-OSS 120B, with streaming, multi-turn conversations, and function calling.

![The Hermes desktop model picker showing a Google Antigravity (AGY) section listing the Gemini 3.x, Claude Sonnet 4.6 and related models](docs/screenshots/model-picker.png)

Antigravity models appear in the Hermes model picker as their own provider
group, alongside every other configured provider.

![A Hermes chat exchange. Asked "hi which modle are u and who are u", the reply is "I am Hermes, your AI assistant created by Nous Research. I am running on Gemini 3.5 Flash (Medium)."](docs/screenshots/identity.png)

Identity is preserved: the assistant answers as Hermes, names Nous Research as
its creator, and reports the Antigravity model underneath only because it was
asked which model it runs on.

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

### 4b. Declare the provider in config.yaml

```bash
python -m hermes_antigravity config
```

`python -m hermes_antigravity setup` already does this, so skip it if you ran
that after installing. It is listed separately because it is easy to miss why
it is needed.

**Hermes resolves providers through two unrelated paths.** Inference uses
`providers/_discover_providers()`, which scans the plugin directory — that is
how step 4 is found. Model *switching* uses `resolve_provider_full()` in
`hermes_cli/providers.py`, whose chain is the built-in provider table, then
models.dev, then `config.yaml`. It never consults the plugin registry.

So a plugin-only install works from the CLI and then fails in the desktop app
with `Unknown provider 'antigravity'` the moment you try to switch models. The
command above adds this to `config.yaml`, which covers the second path:

```yaml
providers:
  antigravity:
    name: Google Antigravity (agy)
    api: http://127.0.0.1:8787/v1
    key_env: HERMES_ANTIGRAVITY_API_KEY
    transport: openai_chat
```

Your existing config is backed up to `config.yaml.bak-antigravity` first, and
re-running is a no-op.

**One thing the plugin cannot do for you here.** It sets
`HERMES_ANTIGRAVITY_API_KEY` at import, which is enough for the CLI, but the
desktop app reads `key_env` from the environment it was launched in. Set it
permanently so the GUI sees it:

```powershell
setx HERMES_ANTIGRAVITY_API_KEY local-bridge
```

Then restart the desktop app.

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
[ok]   config.yaml declares the provider
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

## Identity and memory

Hermes sends its own system message on every request — roughly 34,000 characters
carrying "You are Hermes Agent, created by Nous Research", the instruction to
identify as Hermes rather than the underlying model, the contents of `SOUL.md`,
`memories/USER.md` and `memories/MEMORY.md`, and a tools array. This package
passes all of it through untouched.

Two things support that:

**The agents claim no identity of their own.** `hermes-passthrough` and
`hermes-reader` are written to defer to the prompt's system instructions and are
told explicitly not to introduce themselves as Antigravity, Gemini, or Claude.
An agent that asserted its own persona would compete with Hermes's.

**Direct callers get the context too.** A request arriving with no system
message — `curl`, a script, another OpenAI-compatible client — would otherwise
leave the model with no identity and no memory. The bridge detects that case and
builds a system message from the same files under `$HERMES_HOME`, plus the list
of installed skills from `$HERMES_HOME/skills/`. Each file is capped at 8,000
characters so a large `MEMORY.md` cannot blow the prompt budget on its own.

This never fires on the Hermes path, because Hermes always sends a system
message. Set `HERMES_ANTIGRAVITY_NO_CONTEXT=1` to disable it entirely.

Verified both ways: through Hermes, and via `curl` with no system message, the
answer to "who are you" is "I am Hermes, an intelligent AI assistant created by
Nous Research".

## Note on prompt size

A real Hermes request is about 34,000 characters, which is over the 30,000-byte
argv ceiling this package uses on Windows. Ordinary Hermes traffic therefore
takes the file-handoff path described above rather than argv, and runs under
`hermes-reader`. That is expected, not a fault — it is the reason the file path
exists.

## Cost

`agy` normally injects its own system prompt plus roughly fifty tool definitions into every call. Measured on Windows 11 with `gemini-3.6-flash-low`:

| Request | Agent | Input tokens |
| --- | --- | --- |
| `Say exactly: PONG` | default | 23,684 |
| `Say exactly: PONG` | `hermes-passthrough` | 5,398 |
| Weather question with one tool declared | default | 23,357 |
| Weather question with one tool declared | `hermes-passthrough` | 5,805 |

That is why step 3 exists. There is still a floor of roughly 5k input tokens per request that `agy` adds and this package cannot remove.

## Latency

Measured on Windows 11 with `gemini-3.6-flash-low`, timing a trivial prompt:

| Phase | Time |
| --- | --- |
| `agy` binary startup (`agy --version`) | 0.24s |
| Until `agy` emits its first event | ~10.1s |
| Until the first token of the answer | ~16.5s |
| Total | ~17.7s |

**Most of it is not reachable from here.** The ~10s before the first event is
`agy`'s own session setup, and nothing available from outside the binary moves
it: `--sandbox` costs nothing (18.4s vs 18.8s with and without), and resuming an
existing conversation with `--conversation` saves about 1s (17.6s to 16.6s).

Two things this package does do:

**The model list is cached** for 15 minutes. `agy models` takes about 5s and
Hermes calls `/v1/models` every time the picker opens; cached, it returns in
about 10 microseconds. It also rides out the occasional stall — one `agy models`
call during testing hung past 60s, which would otherwise have emptied the picker.

**Streaming is real.** Text deltas are forwarded as `agy` produces them rather
than buffered until the process exits. On a 120-word answer through the bridge,
first visible text arrived at 17.95s streamed against 19.95s buffered, and the
gap widens with answer length. Streaming is skipped when the request declares
tools, because a tool call is not decidable until the reply is complete.

So expect roughly 15-20s for a short answer. That is the floor `agy` imposes,
not overhead this package adds.

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
| `HERMES_ANTIGRAVITY_NO_CONTEXT` | Any non-empty value disables the identity/memory fallback | unset |
| `HERMES_HOME` | Where Hermes keeps `SOUL.md`, `memories/`, and `skills/` | `%LOCALAPPDATA%\hermes`, else `~/.hermes` |
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
| `Unknown provider 'antigravity'` when switching models | Model switching does not read the plugin registry, only `config.yaml`. Run `python -m hermes_antigravity config` and restart. |
| Works in the CLI, fails in the desktop app | Same cause as above, plus `HERMES_ANTIGRAVITY_API_KEY` must exist in the environment the GUI was launched from. `setx HERMES_ANTIGRAVITY_API_KEY local-bridge`, then restart the app. |
| Plugin not discovered at all | It went into the wrong directory. Hermes home is often `%LOCALAPPDATA%\hermes` on Windows, not `~/.hermes` — check `echo $HERMES_HOME` and redo step 4. |
| `hermes-antigravity: command not found` | The console script directory is not on `PATH`. Use `python -m hermes_antigravity` instead. |
| Connection refused from Hermes | Autostart could not find an interpreter with the package. Set `HERMES_ANTIGRAVITY_PYTHON`, or start the bridge yourself with `python -m hermes_antigravity serve`. |
| `'agy models' returned no models` | `agy` is not logged in. Run `agy auth login`. |
| Terminal windows appear on Windows | Fixed in 0.1.2. Two separate causes: the plugin used `DETACHED_PROCESS`, which still lets Windows open a console, and the bridge spawned `agy` with no flags at all — since `agy` is a console app and the bridge runs under `pythonw`, every model call opened a window. Upgrade the package and re-copy step 4. |

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

The test suite covers prompt sizing, the argv/file switch, and every translation path. It does not call `agy`, so it costs no quota and needs no login.

## License

MIT
