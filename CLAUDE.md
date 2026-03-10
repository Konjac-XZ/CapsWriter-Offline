# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working inside this fork of CapsWriter-Offline.

## Project Overview

**Important:** This repository has been slimmed down to a **client-only, cloud-backed transcription tool**. The upstream self-hosted server, translation flows, and hotword system were removed. The remaining code captures audio on Windows, forwards it to remote transcription providers (OpenAI-compatible), and types the results back into the OS.

### Current Architecture
- **Client only:** No Python server, WebSocket bridge, or local ASR models remain.
- **Cloud transcription:** Providers are defined under `config/providers/*.yaml` and accessed through OpenAI-style HTTP APIs.
- **Focused features:** Recording, cloud clipboard sharing, markdown journaling, and provider switching.
- **Minimal docs:** Historical documentation (e.g. `readme.md`) was deleted; this file is the active orientation guide.

### Active Components
- `start_client_gui.py` / `start_client_gui.exe` – main Qt GUI.
- `core_client.py` – CLI client that shares the same pipeline; preferred for debugging.
- `src/client_*`, `src/openai_transcribe_*`, `src/transcribe/api.py` – audio capture, payload building, provider dispatch, output typing.
- Provider system (`config/providers/*.yaml`, `src/provider_config.py`, `provider_switch.py`).
- `config.toml` (client section) – hotkeys, audio saving, clipboard behaviour, etc. The server section is vestigial and slated for removal.
- `edit_config_gui.py` – GUI wrapper around provider/model settings. The old client configuration page was deleted; tweak client defaults directly in `config.toml` for now.

### Removed / Legacy Items
- `core_server.py`, `start_server_gui.py`, and all `src/server_*` helpers – deleted.
- Translation and hotword modules, shortcuts, configuration toggles, and text assets – deleted.
- Extra requirement files (`requirements-editconfiggui.txt`, `requirements-server.txt`) – deleted.
- Historical docs, screenshots, and README – deleted.
- Bundled `runtime/` and `site-packages/` directories – deleted in favor of uv-managed virtual environment.

## Core Functionality

- **Audio capture:** Global hotkeys (CapsLock by default) trigger recordings via `src/client_shortcut_handler.py`. Drag-and-drop in the GUI or passing a file to `core_client.py` uploads audio files instead of live mic input.
- **Cloud transcription:** Audio is converted with `src/openai_transcribe_audio.py` and submitted through `src/transcribe/api.py`, which routes to OpenAI-compatible, Replicate, ElevenLabs, Soniox, or Alibaba Cloud endpoints depending on the active provider.
- **Text output:** Streaming updates appear in-console/GUI; final results are spaced with `pangu`, optional simplified↔traditional conversion happens in `src/client_recv_result.py`, and clipboard/cloud clipboard helpers live under `src/cloud_clipboard*.py`.
- **Logging:** Optional audio file persistence and markdown journaling (`src/client_write_md.py`) remain available, though keyword diary logic was removed with hotwords.

## Configuration System

- **Provider configs:** YAML files under `config/providers/` define API metadata, prompts, base URLs, and defaults per provider.
- **Provider manager:** `src/provider_config.py` rewrites a `.provider_state.json` cache and sets environment variables so the client picks up the selected backend.
- **`config.toml`:** Still supplies client UX defaults. Server keys are legacy; avoid touching them unless you are finishing their removal.
- **Config editor:** `edit_config_gui.py` now presents provider/model pages only. Client settings must be edited manually until the editor is rebuilt.

### Provider YAML Snapshot

```yaml
name: "Provider Name"
type: openai | replicate | elevenlabs | soniox | alibaba
description: "Human-readable summary"
settings:
  api_key: "${ENV_VAR_OR_PLACEHOLDER}"
  base_url: "https://api.example.com"
  model: "gpt-4o-transcribe"
  prompt: |
    Optional multi-line system prompt
enabled: true
```

YAML values may reference environment variables (`${VAR}`) so secrets can stay outside the repo.

## Running & Testing

### Initial Setup

```bash
# Install uv if not already installed
pip install uv

# Install dependencies
uv sync
```

### Running the Application

```bash
# GUI client
uv run python start_client_gui.py

# CLI client for development/testing
uv run python core_client.py [optional-media-file]

# Provider/config editor
uv run python edit_config_gui.py
```

Smoke-test checklist:
- Ensure `OPENAI_API_KEY` (or provider-specific env vars) is set; provider manager can stub demo keys for local tinkering.
- Tap CapsLock (or your configured shortcut) to verify streaming transcription end-to-end.
- Drag an audio file onto the GUI or pass a file to `core_client.py` to exercise batch uploads.
- Use both the GUI provider page and `provider_switch.py` to confirm provider switching and prompt overrides.

### Provider Management CLI

```bash
# List configured providers
python provider_switch.py --list

# Switch active provider (updates env + cache)
python provider_switch.py --switch qianduoduo

# Show current provider
python provider_switch.py --current

# Reload YAML after manual edits
python provider_switch.py --reload
```

## Dependencies

- Install dependencies with `uv sync`
- Dependencies are managed via `pyproject.toml`
- A lockfile (`uv.lock`) ensures reproducible builds
- No additional requirement files remain

## Development Notes

- **Cloud-first pipeline:** Everything ultimately runs through `transcribe_audio` or provider-specific HTTP helpers. When debugging transcription issues, start there.
- **No translation/hotword hooks:** Ensure new features do not resurrect the removed shortcuts or config keys unless explicitly requested.
- **Focus areas:** Client UX (GUI + CLI), provider management, and progressive cleanup of leftover server-era config.
- **Virtual environment:** All dependencies are isolated in `.venv/`, managed by uv.

---

- Do not try to revive or run the deleted server stack; it no longer has supporting modules or dependencies.
