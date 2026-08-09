# CapsWriter-Offline

Cloud-backed transcription tool for Windows with support for multiple providers.

## Quick Start

1. Install uv:
   ```bash
   pip install uv
   ```

2. Install dependencies:
   ```bash
   uv sync
   ```

3. Configure your API key:
   ```bash
   # Set your provider API key (e.g., OpenAI or Gemini)
   set OPENAI_API_KEY=your_key_here
   # or
   set GEMINI_API_KEY=your_key_here
   ```

4. Run the GUI:
   ```bash
   uv run python start_client_gui.py
   ```

## Startup Profiling

Use these tools to diagnose slow startup without changing normal behavior.

1. Install optional profiling dependencies:
   ```bash
   uv sync --group profiling
   ```

2. Profile startup with built-in `cProfile` (works even without extra deps):
   ```bash
   uv run python start_client_gui.py --profile-startup --profile-tool cprofile --profile-duration-ms 8000
   ```

3. Try richer traces (if installed):
   ```bash
   uv run python start_client_gui.py --profile-startup --profile-tool pyinstrument --profile-duration-ms 8000
   uv run python start_client_gui.py --profile-startup --profile-tool viztracer --profile-duration-ms 8000
   uv run python start_client_gui.py --profile-startup --profile-tool yappi --profile-duration-ms 8000
   ```

4. Output files are written to `profiles/startup/` by default. You can override via:
   ```bash
   uv run python start_client_gui.py --profile-startup --profile-output profiles/startup/my_run --profile-tool cprofile
   ```

Environment variable mode is also supported:

```bash
set CW_PROFILE_STARTUP=1
set CW_PROFILE_TOOL=pyinstrument
set CW_PROFILE_DURATION_MS=8000
uv run python start_client_gui.py
```

### Fast startup mode (default)

`qt_material` theme application is disabled by default to reduce startup latency.

- Enable theme explicitly:
   ```bash
   set CW_ENABLE_QT_MATERIAL=1
   uv run python start_client_gui.py
   ```
- Optional delayed apply (milliseconds, default `3000`):
   ```bash
   set CW_THEME_DELAY_MS=5000
   uv run python start_client_gui.py
   ```

- Tray menu warm-up is deferred by default (15s). Optional override:
   ```bash
   set CW_TRAY_WARMUP_DELAY_MS=30000
   uv run python start_client_gui.py
   ```

## Build Native Launcher (Windows)

To build a silent `start_client_gui.exe` launcher (no console window), open a **Developer Command Prompt for Visual Studio** in the repository root and run:

```bat
build_gui.bat
```

The generated `start_client_gui.exe` only starts:

```bat
uv run python start_client_gui.py
```

Launcher behavior: before starting, it terminates any existing running `start_client_gui.py` process, then starts a fresh one.

## Configuration

- Providers: Edit YAML files in `config/providers/`
- Provider YAML can declare multiple transcription models. The main GUI selects a
  unique `model · provider` pair and derives the live-audio switch from that
  model's capabilities. See [provider/model configuration](docs/provider-model-config.md)
  for schema-v2 examples and the legacy migration command.
- Gemini: Fill `config/providers/gemini.yaml` (or set `GEMINI_API_KEY`/`GOOGLE_API_KEY`) and enable the provider before use
- Qwen Audio Legacy: The `qwen-audio-legacy` realtime path uses the official `OmniRealtimeConversation` SDK for `qwen3-asr-flash-realtime`; disabling `流式音频` keeps its existing official-SDK final-file path. Set `workspace_id` and clear an explicit `realtime_url` to use the region-specific workspace endpoint automatically.
- Qwen Audio 3.0 ASR: Set `DASHSCOPE_API_KEY`, then select `阿里百炼 (qwen-audio)` in the GUI. The provider uses the official `Recognition` SDK with `qwen-audio-3.0-asr-flash-streaming` by default and falls back to final-file HTTP transcription when streaming fails. The GUI `流式音频` switch controls this behavior. Optionally fill `workspace_id` in `config/providers/qwen_audio_3.yaml` to use the dedicated Beijing or Singapore workspace endpoint.
  The GUI user lexicon is sent as inline Qwen hotwords on the next recording (`use_user_lexicon: true`, default weight `4`). Provider-specific `vocabulary` entries add terms or override weights. Invalid/over-limit terms are skipped with a console diagnostic.
  Qwen ASR context is independently configurable under `asr_context`: by default it reuses the shared capture task to send up to four finalized input-history messages plus one caret-local textbox excerpt before the audio message. Each message is capped at 400 characters; vision summaries and LLM polish instructions are never sent. The upload waits at most `capture_timeout_ms` for capture, then continues without context.
  To inspect the outgoing HTTP payload or streaming SDK construction parameters, set both `debug: true` and `log_request_payload: true` in the provider YAML. The debug copy keeps model, context, vocabulary, and parameters, but omits all audio content and never includes request headers or the API key.
- Client settings: Edit `config.toml`
- Optional LLM polishing: select the LLM backend with `provider` in `config/polish/polish.yaml`. Existing configurations that omit it remain compatible and default to `openai_compatible`. For a generic OpenAI-compatible endpoint, set these credentials in `.env` or `.env.local`:
   ```env
   LLM_POLISH_BASE_URL=https://api.openai.com
   LLM_POLISH_API_KEY=your_key_here
   ```
   Runtime state is stored in `%LOCALAPPDATA%\CapsWriter-Offline\State\capswriter.db`
   using SQLite WAL mode. The current task constraint, finalized-input history,
   and daily input totals survive restarts. Existing `finalized_history.json` and
   `daily_input.json` files are imported once and retained as
   `.migrated.bak`; configuration, credentials, and audio caches remain outside
   the database.
   ```yaml
   provider: openai_compatible
   model: gpt-4.1-mini
   openai_compatible:
     extra_body: {}
   ```
   To use OpenRouter's official Python SDK, set `OPENROUTER_API_KEY`, select `openrouter`, and use OpenRouter's `author/model` model slug. `LLM_POLISH_API_KEY` remains a fallback for migration:
   ```env
   OPENROUTER_API_KEY=your_openrouter_key_here
   ```
   ```yaml
   provider: openrouter
   model: deepseek/deepseek-v3.2
   openrouter:
     base_url: https://openrouter.ai/api/v1
     site_name: CapsWriter-Offline
     site_url: ''
     routing:
       only: [friendli]
       allow_fallbacks: false
       require_parameters: true
       data_collection: deny
     reasoning:
       enabled: false
   ```
   The `openrouter.routing` mapping is passed to OpenRouter's `provider` request parameter. It supports OpenRouter routing fields such as `order`, `only`, `ignore`, `allow_fallbacks`, `require_parameters`, `data_collection`, `zdr`, `quantizations`, `sort`, and `max_price`; use exact provider slugs from OpenRouter. `order` prioritizes providers, while `only` restricts requests to them. Setting `allow_fallbacks: false` prevents routing to providers outside the selected route.
   The provider layer streams by default and falls back to one non-streaming request when streaming is unavailable. Polishing runs before regex replacement and whitespace reformatting in the live microphone pipeline.
   Correction-based personalization is opt-in. When the following section is
   present, trusted TSF post-commit edits are retained in the local SQLite state
   database, summarized with the configured polish LLM while the client is idle,
   and retrieved by keyword for later polish requests:
   ```yaml
   personalization:
     enabled: true
     reflection:
       enabled: true
       poll_seconds: 30
       settle_seconds: 120
       batch_size: 6
       lease_seconds: 180
       max_input_chars: 8000
       max_output_tokens: 2048
       temperature: 0.2
       retry_base_seconds: 60
       retry_max_seconds: 3600
     retrieval:
       enabled: true
       max_preferences: 5
       max_prompt_chars: 1600
   ```
   Clearing recent-output history does not erase queued corrections or learned
   preferences. Each settled reflection batch sends its ASR, committed, and
   user-corrected text to the same provider/model used for polishing. Remove or
   disable the section if durable correction records and background requests are
   not desired.
   LLM polish also runs a conservative Chinese smart-quotes post-processor by default (`smart_quotes.enabled=true`) to turn abused straight quotes into Chinese quotes while protecting Markdown code, inline code, URLs, paths, HTML, math, frontmatter, and structured text.
   When `textbox_context.enabled=true`, the registered TSF Speech TIP is the preferred context source. It reads a bounded window around the insertion point in a read-only edit session and returns text, caret, selection, and the actual host process without changing the document. If no foreground TIP responds, capture falls back to Windows UI Automation in this order: focused element → `TextPattern` → `ValuePattern` → `LegacyIAccessible`. The more intrusive `Ctrl+A` / `Ctrl+C` clipboard probe is disabled by default; set `textbox_context.clipboard_fallback_enabled=true` only when that fallback is explicitly wanted. `textbox_context.max_tokens` limits the attached textbox context with a lightweight token estimate; the default is 600. `tsf_speech_tip_context_timeout_ms` controls the initial TIP/context wait independently from composition ACK timing.
   Press the client hotkey configured by `toggle_textbox_context_shortcut` in `config.toml` (default: `f16`) to quickly toggle `textbox_context.enabled`; set it to an empty string to disable the toggle hotkey.
   Set `textbox_context.debug=true` in `config/polish/polish.yaml` to log why each UIA fallback stage succeeded, returned empty text, or skipped/used the clipboard fallback. Context logs record source/host/length but never the captured text.
- Use `python provider_switch.py --list` to see available providers

## Documentation

- `CLAUDE.md`: Technical architecture and component details
- `AGENTS.md`: Development workflow and agent integration

## Requirements

- Python 3.11+
- Windows OS
- uv package manager
