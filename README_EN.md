<div align="center">

# Debata_Agent

**A chatbot framework that makes virtual characters feel alive**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)
[![WebUI](https://img.shields.io/badge/UI-WebUI%20%28FastAPI%29-blueviolet.svg)](#)
[![Tests](https://img.shields.io/badge/tests-1697%20passed-brightgreen.svg)](#testing-and-verification)

</div>

---

> "What if the thing behind `/v1/chat/completions` were a person?"

Debata_Agent does not come with a fixed personality. You give it one, and it
becomes that person. The project is named after Debata, the first character
built with it.

<div align="center">
<img src="personas/debata/角色人设主图.png" width="600" alt="Debata character design">

*Debata — 17, short dark hair, charcoal hoodie. Sharp-tongued with close friends, quietly caring underneath.*
</div>

## Contents

- [Why it feels different](#why-it-feels-different)
- [2026-10 token-efficiency refactor](#2026-10-token-efficiency-refactor)
- [Key features](#key-features)
- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Running in the background](#running-in-the-background)
- [Configuration and data directory](#configuration-and-data-directory)
- [Project layout](#project-layout)
- [Testing and verification](#testing-and-verification)
- [Documentation](#documentation)
- [Design decisions](#design-decisions)
- [Security and privacy](#security-and-privacy)
- [License and acknowledgements](#license-and-acknowledgements)

---

## Why it feels different

| | Typical AI bot | Debata_Agent |
|---|---|---|
| Context | Per conversation | **Unified timeline plus state and relationship context** |
| Replies | One long message | 3-7 short messages, 60% under 12 characters |
| Timing | Always instant | Can "miss" messages, reply hours later |
| Tone | "Of course! 😊" | "?" / "nah" / "6" / "whatever" |
| Style | Same for everyone | **Switches with the relationship** (close friend / elder / stranger) |
| Initiative | Passive | Decides on its own when to speak |
| Memory | Last few messages | Important memories + local archive + optional RAG |
| Config | Edit code | **Browser wizard + settings, instant save** |

## 2026-10 token-efficiency refactor

The goal was not to change languages. Token cost is driven by prompts and
context, not by Python versus Go. This refactor makes the prompt spend tokens on
useful context instead of repeating stale runtime snapshots.

Measured with `scripts/benchmark_prompt_tokens.py` on a real instance:

| Metric | Before | After | Change |
|---|---:|---:|---:|
| Active history tokens | 185,173 | 43,248 | **-76.6%** |
| Tool schema tokens | 9,817 | 4,740 | **-51.7%** |
| Estimated prompt tokens | 206,768 | 59,766 | **-71.1%** |
| Visible records | 568 | 296 | -47.9% |

What changed:

- **Non-destructive model view** (`core/model_view.py`): trims the view sent to
  the model only. Raw history, archive, and event store stay untouched. Old
  `task_context`, send receipts, and `no_action` pairs no longer accumulate.
- **Tool schema profiles**: `compact` by default. High-frequency tools expose
  core fields; low-frequency/high-risk tools become stubs and are expanded via
  `tool_search detail=full`.
- **Auto-finish after a successful send**: avoids an extra `no_action` model
  turn just to close the loop.
- **More accurate metering**: canonical JSON for messages + tools, persistent
  per-provider/model calibration, and estimate-error reporting in the dashboard.
- **Leaner runtime**: summary-mode KV diagnostics by default; caches for text
  encoding, tool schemas, and tool diagnostics.

## Key features

- **Human chat patterns**: short message waterfalls, split sends, no forced
  goodbyes, no customer-service voice; off-topic, changes of mind, and
  forgetting are allowed.
- **Relationship matrix**: one persona, different tone and boundaries for close
  friends, elders, strangers, and group members.
- **Persona state and background mind**: age brackets, energy, satiety, mood,
  social needs, sleep/meal recovery with formula fallback.
- **Structured context**: events, bounded episode fragments, state, user
  profiles, short-term effects, clues, todos, and relevant long-term memory.
- **Long-term memory**: zero-config file mode, or RAG mode with a background
  vector index and semantic retrieval.
- **14 built-in provider presets**: OpenAI, Anthropic Claude, DeepSeek, GLM,
  Qwen, Volcengine, Gemini, Moonshot, SiliconFlow, OpenRouter, Groq, Together,
  xAI, Xiaomi MiMo; custom OpenAI/Anthropic-compatible gateways are supported.
- **Multi-agent design**: chat, proactive thinking, summarization, persona
  management, social decisions, and subconscious consolidation can use
  different models.
- **WebUI**: setup wizard + 9 pages (overview, chats, memory, persona, logs,
  personas, models, blacklist, settings) with instant-save settings.
- **Optional capabilities**: vision, TTS, weather, web search, local VoxCPM2 /
  sentence-transformers plugins; QQ voice transcription uses NapCat.
- **Security**: AES-256-GCM + RSA-2048 encrypted secrets, OS keyring preferred,
  token auth for the WebUI.
- **Background running**: systemd user/system services, tmux, screen, and nohup,
  with a one-command installer.

## Requirements

- **Python** 3.11+ (3.12 recommended)
- **uv** for dependencies and virtual environments
- **NapCat** ([install guide](https://napneko.github.io/guide/start-install)) — QQ protocol bridge
- **LLM API key** — DeepSeek recommended (good Chinese, affordable, strong KV cache)
- Windows / macOS / Linux; servers can run headless

## Quick start

### 1. Get the code and install dependencies

```bash
git clone https://github.com/binglang001/Debata_Agent.git
cd Debata_Agent

uv sync
```

Or with SSH:

```bash
git clone git@github.com:binglang001/Debata_Agent.git
cd Debata_Agent
uv sync
```

### 2. Start the WebUI

```bash
uv run main.py
```

The terminal prints the access URLs and a token. Open either URL in a browser.
On first launch, the setup wizard configures the model, persona, NapCat, and
background agents.

### 3. Useful flags

```bash
uv run main.py --port 9000          # different port
uv run main.py --host 127.0.0.1     # local access only
uv run main.py --token my-token     # fixed WebUI token
uv run main.py --no-auth            # disable auth (trusted LAN only)
uv run main.py --no-gui             # headless runtime, no WebUI
uv run main.py --no-gui --setup     # CLI setup wizard
uv run main.py --napcat             # reconfigure NapCat only
uv run main.py --test-adapter       # test NapCat connection for 5 seconds
```

## Running in the background

Install once, then use the short `debata` commands:

```bash
cd ~/Debata_Agent
bash scripts/install_systemd_user.sh     # one-time install + start

debata start       # start (auto-installs the service on first run)
debata stop        # stop
debata log         # follow logs
debata enable      # enable boot autostart (and start now)
debata disable     # disable boot autostart
debata status      # show status
debata restart     # restart
```

- `debata enable` also tries to enable linger; if it fails, run
  `sudo loginctl enable-linger "$USER"`.
- Without a systemd user session (containers, WSL, minimal systems), the CLI
  falls back to a PID-file background mode: `start/stop/restart/status/log`
  still work. `enable` starts the process but cannot configure boot autostart;
  use a container restart policy or cron `@reboot` instead.
- Starts the WebUI and QQ runtime by default. Headless:
  `bash scripts/install_systemd_user.sh --headless`.
- If `debata` is not on PATH, use `./debata start`.
- NapCat must be kept running separately.

Full guide: **[docs/background_running.md](docs/background_running.md)**.
System-level template: `scripts/systemd/debata-agent.system.service`.

## Configuration and data directory

Default data directory:

```text
~/.local/share/Debata_Agent/instances/default/
├── config.yaml          # main config (gitignored)
├── secrets.enc          # encrypted API keys
├── logs/debata.log      # application log
├── memory/              # history, important memories, usage records
├── vector/              # RAG vector store
├── workspace/           # AI read/write sandbox
├── providers/presets/   # user-defined provider presets
└── webui_token.txt      # WebUI access token
```

Legacy mode (project-local `.debata-dev-data-root`) still uses `data/`.
`DEBATA_DATA_ROOT` can override the data root; old data is migrated on first start.

Common settings (WebUI → Settings):

| Setting | Default | Meaning |
|---|---|---|
| `behavior.context.max_context_tokens` | 131072 (live instance) | Working context budget, not the model hard limit |
| `behavior.summarize.trigger_at_context_percent` | 50 | Trigger rolling summary at 50% of the view budget |
| `behavior.summarize.target_after_context_percent` | 30 | Target active-window size after compaction |
| `behavior.tool_schema_profile` | `compact` | Tool schema profile; `full` is the rollback path |
| `behavior.context.model_view.enabled` | `true` | Non-destructive model view; disable for full history |
| `behavior.auto_finish_after_send` | `true` | Finish the tool loop after a successful send |
| `behavior.kv_prompt_diagnostics` | `summary` | KV diagnostics level; `full` for debugging |

## Project layout

```text
Debata_Agent/
├── adapters/               Channel adapters (NapCat)
├── providers/              LLM providers, 14 presets, capability matrix
├── agents/                 Chat / proactive / summary / persona / social / subconscious
├── mind/                   Persona state, profiles, relationships, clues, todos, audits
├── memory/                 History, important memory, archive, RAG, usage stores
├── tools/                  AI tools, schema profiles, result shrinking
├── features/               Vision / TTS / weather / search / embedding
├── core/                   Event bus, pipelines, runtime, model view, token calibration
├── app_config/             Config, paths, secret encryption
├── webui/                  WebUI (FastAPI backend + static frontend)
├── plugins/                Local model plugins (VoxCPM2 / embedding)
├── utils/                  CQ parsing, token estimation, proxy, time
├── personas/               Persona files (debata tracked; personal ones gitignored)
├── scripts/                Benchmarks, systemd installer and templates
├── docs/                   Tutorials, architecture, background running, development docs
├── tests/                  Unit / integration tests
└── main.py                 Entry point (WebUI by default)
```

## Testing and verification

```bash
uv sync --extra dev

.venv/bin/python -m pytest tests/ -q --ignore=tests/test_kv_cache_real.py
.venv/bin/ruff check .
```

Local result: **1697 passed**, `ruff check .` clean (Linux / Python 3.12).
The CI matrix is defined in [.github/workflows/test.yml](.github/workflows/test.yml)
(Ubuntu + Windows, Python 3.11 / 3.12).

Prompt-token benchmark (read-only, no model calls):

```bash
.venv/bin/python scripts/benchmark_prompt_tokens.py \
  --db ~/.local/share/Debata_Agent/instances/default/memory/小堡/小堡.db \
  --persona 小堡 --model deepseek-flash
```

## Documentation

- [Getting Started](docs/getting_started.md) — step-by-step with screenshots
- [Background Running Guide](docs/background_running.md) — systemd / tmux / screen / nohup
- [Features](docs/features.md) — chat style, memory, persona, WebUI
- [Persona Writing Guide](docs/persona_writing_guide.md) — writing a persona that feels alive
- [Architecture Overview](docs/architecture.md) — model view, token metering, tool profiles
- [KV Cache Benchmark](docs/kv_cache_benchmark.md) — hit-rate data and post-refactor estimates
- [Provider Development](docs/provider_development.md) — adding LLM backends
- [Adapter Development](docs/adapter_development.md) — adding chat platforms
- [WebUI Guide](docs/webui.md) — pages, API surface, extending the console
- [UI Style Guide](docs/ui_style_guide.md) — design principles and color specs

## Design decisions

- **One stable system prompt + XML sections**: stable prefix first for better KV
  cache hits, lower token cost, and lower latency.
- **Volatile blocks after history**: `task_context`, long-term memory, rolling
  summary, and RAG are appended after the append-only history so they do not
  invalidate the whole prefix.
- **Model view separate from on-disk history**: stale runtime snapshots can be
  hidden from the prompt without deleting raw history or archives.
- **Tool profiles instead of exposing everything**: high-frequency tools expose
  core fields; low-frequency tools are expanded on demand.
- **Imperfection by design**: going off-topic, changing one's mind, and
  forgetting are part of the experience, not bugs.
- **Secrets without passwords**: OS keyring + AES-GCM, backed by cryptography.
- **Configuration in the UI where possible**: settings save instantly; persona
  files remain hand-editable.
- **Blacklist over whitelist**: block specific people or groups instead of
  tightening the whole open policy.

## Security and privacy

- `config.yaml`, `secrets.enc`, `data/`, non-debata personas, logs, and the
  workspace are gitignored and never committed.
- The WebUI uses token auth by default. `--no-auth` is only for a fully trusted
  LAN.
- For public access, use Nginx/Caddy with HTTPS, or bind to `127.0.0.1`.
- Run system services as a dedicated user, never as root.
- Chat history, personas, and configuration are personal data; back up and
  sanitize before sharing.

## License and acknowledgements

This project is maintained by
[binglang001/Debata_Agent](https://github.com/binglang001/Debata_Agent)
and its contributors. Original copyright and license notices are preserved.

[Apache 2.0](LICENSE) — free for commercial use, modification, and distribution.

Acknowledgements:

- [NapCat](https://github.com/NapNeko/NapCatQQ) — modern QQ protocol bridge
- [FastAPI](https://fastapi.tiangolo.com/) — WebUI HTTP / WebSocket framework
- [Uvicorn](https://www.uvicorn.org/) — ASGI server
- [uv](https://github.com/astral-sh/uv) — Python dependency and environment manager
- Everyone who filed issues, opened pull requests, or helped with testing

---

<div align="center">

**「砚台旁有墨，纸上有空」**

*Ink by the inkstone, space on the paper.*

</div>
