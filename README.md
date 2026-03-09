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
- Client settings: Edit `config.toml`
- Optional LLM polishing: create a `.env` or `.env.local` file in the repo root with:
   ```env
   LLM_POLISH_ENABLED=1
   LLM_POLISH_BASE_URL=https://api.openai.com
   LLM_POLISH_API_KEY=your_key_here
   LLM_POLISH_MODEL=gpt-4.1-mini
   # Optional
   # LLM_POLISH_TIMEOUT=8
   # LLM_POLISH_TEMPERATURE=0.1
   # LLM_POLISH_MAX_OUTPUT_TOKENS=512
   ```
   This feature uses the OpenAI-compatible Responses API in non-streaming mode and runs before regex replacement and whitespace reformatting in the live microphone pipeline.
- Use `python provider_switch.py --list` to see available providers

## Documentation

- `CLAUDE.md`: Technical architecture and component details
- `AGENTS.md`: Development workflow and agent integration

## Requirements

- Python 3.11+
- Windows OS
- uv package manager
