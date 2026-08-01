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
- Gemini: Fill `config/providers/gemini.yaml` (or set `GEMINI_API_KEY`/`GOOGLE_API_KEY`) and enable the provider before use
- Qwen Audio 3.0 ASR: Set `DASHSCOPE_API_KEY`, then select `阿里百炼 Qwen Audio 3.0` in the GUI. The provider uses final-file HTTP transcription; optionally fill `workspace_id` in `config/providers/qwen_audio_3.yaml` to use the dedicated Beijing or Singapore workspace endpoint.
  The GUI user lexicon is sent as inline Qwen hotwords on the next recording (`use_user_lexicon: true`, default weight `4`). Provider-specific `vocabulary` entries add terms or override weights. Invalid/over-limit terms are skipped with a console diagnostic.
  Qwen ASR context is independently configurable under `asr_context`: by default it reuses the shared capture task to send up to four finalized input-history messages plus one caret-local textbox excerpt before the audio message. Each message is capped at 400 characters; vision summaries and LLM polish instructions are never sent. The upload waits at most `capture_timeout_ms` for capture, then continues without context.
  To inspect the outgoing JSON request, set both `debug: true` and `log_request_payload: true` in the provider YAML. The debug copy keeps model, context, vocabulary, and parameters, but removes the entire `input_audio` value and never includes request headers or the API key.
- Client settings: Edit `config.toml`
- Optional LLM polishing: set `LLM_POLISH_BASE_URL` and `LLM_POLISH_API_KEY` in a `.env` or `.env.local` file in the repo root, then edit `config/polish/polish.yaml` for feature settings such as `enabled`, `model`, `timeout`, and `textbox_context`:
   ```env
   LLM_POLISH_BASE_URL=https://api.openai.com
   LLM_POLISH_API_KEY=your_key_here
   ```
   This feature uses the OpenAI-compatible Chat Completions API in non-streaming mode and runs before regex replacement and whitespace reformatting in the live microphone pipeline.
   LLM polish also runs a conservative Chinese smart-quotes post-processor by default (`smart_quotes.enabled=true`) to turn abused straight quotes into Chinese quotes while protecting Markdown code, inline code, URLs, paths, HTML, math, frontmatter, and structured text.
   When `textbox_context.enabled=true`, it tries Windows UI Automation in this order: focused element → `TextPattern` → `ValuePattern` → `LegacyIAccessible`; if those all fail, it finally falls back to a more intrusive `Ctrl+A` / `Ctrl+C` clipboard probe before attaching a truncated snapshot as extra reference context. Controls that support `TextPattern2` also attach `<|caret|>` at the current insertion point, with optional selection boundary markers when UIA exposes a text selection. `textbox_context.max_tokens` limits the attached textbox context with a lightweight token estimate; the default is 600.
   Press the client hotkey configured by `toggle_textbox_context_shortcut` in `config.toml` (default: `f16`) to quickly toggle `textbox_context.enabled`; set it to an empty string to disable the toggle hotkey.
   Set `textbox_context.debug=true` in `config/polish/polish.yaml` to log why each UIA stage succeeded, returned empty text, or fell through to clipboard fallback.
- Use `python provider_switch.py --list` to see available providers

## Documentation

- `CLAUDE.md`: Technical architecture and component details
- `AGENTS.md`: Development workflow and agent integration

## Requirements

- Python 3.11+
- Windows OS
- uv package manager
