# Repository Guidelines

## Project Structure & Module Organization
- `core_server.py` runs the speech-to-text back-end, while `core_client.py` and `start_client_gui*.py` expose CLI and GUI clients for keyboard-driven capture.
- Reusable logic lives in `util/` (websocket streams, GUI/helpers, clipboard integrations); extend these modules first when adding shared behavior.
- Configuration defaults sit in `config.toml` and supporting schemas under `config/`; UI art and documentation assets live in `assets/`.
- Models and cached downloads live under `models/` and `downloads/`; keep large binaries out of commits unless intentionally updated.

## Build, Test, and Development Commands
- `uv sync` installs all dependencies into a managed virtual environment.
- `uv run python core_server.py` starts the offline ASR server using the model selected in `config.toml` (note: server functionality has been removed).
- `uv run python core_client.py` launches the microphone listener; pass a media path to transcribe a file instead of live input.
- `uv run python start_client_gui.py` opens the Qt UI for verifying changes.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation, snake_case for functions/modules, and CapWords for Qt/PySide widget classes.
- Prefer explicit type hints and structured logging via `rich` and `typer`; reserve bare `print` calls for bootstrap diagnostics.
- Keep configuration keys lowercase_with_underscores to align with `config.Config` parsing.

## Testing Guidelines
- Use `python test_replicate.py <audio>` to exercise external provider uploads; mock network calls when promoting these flows to automated tests.
- Place new automated checks in `tests/` and target async flows with `pytest` plus `pytest-asyncio`; mirror filenames from `util/` for traceability.
- Verify GUI edits by running `start_client_gui.py`, then capture before/after screenshots of modified dialogs.

## Commit & Pull Request Guidelines
- Follow the existing Conventional Commit pattern (`feat:`, `refactor:`, `chore:`) and keep subject lines within 72 characters.
- Reference related issues, list manual test steps (server, client, GUI), and attach screenshots for UI-visible changes.
- Call out model or config migrations in the PR description so maintainers can refresh packaged assets.

## Configuration & Security Notes
- Never commit API tokens, personal audio, or `.env` files; document required variables instead.
- Record every change to `config.toml` defaults and provide migration snippets to keep packaged binaries in sync with source.

## Tooling & Shell Usage
- Prefer the bundled bash helpers (`bash -lc`) when invoking shell commands; always set the `workdir` parameter.
- Use `rg`/`rg --files` for searches; fall back only if unavailable.
- Use the `apply_patch` to edit files
- Avoid PowerShell-specific commands.
