# Repository Guidelines

## Project Structure & Module Organization
- `core_server.py` runs the speech-to-text back-end, while `core_client.py` and `start_client_gui*.py` expose CLI and GUI clients for keyboard-driven capture.
- Reusable logic lives in `src/` (websocket streams, GUI/helpers, clipboard integrations); extend these modules first when adding shared behavior.
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
- Use `uv run ruff check .` frequently while editing Python, and run `uv run ruff format --check .` before committing; apply `uv run ruff format <paths>` when formatting is needed.
- Use `uv run ty check` frequently to catch type regressions, especially after changing shared data structures, async task flow, provider interfaces, or test doubles.
- Use `python test_replicate.py <audio>` to exercise external provider uploads; mock network calls when promoting these flows to automated tests.
- Place new automated checks in `tests/` and target async flows with `pytest` plus `pytest-asyncio`; mirror filenames from `src/` for traceability.
- Verify GUI edits by running `start_client_gui.py`, then capture before/after screenshots of modified dialogs.
- Computer Use is optional and should be used only when GUI interaction is
  necessary for the requested task and the user has not asked to keep control
  of the desktop. A build or deployment alone does not require Computer Use.
- Whenever a compiled Windows GUI is rebuilt or redeployed and will be opened
  with Computer Use for testing, reset the Node REPL with `node_repl.js_reset`
  before interacting with the rebuilt application. Treat every build/deploy as
  invalidating the previous Node REPL state, exec context, window handles,
  screenshots, and accessibility element indices.
- After that reset, import `@oai/sky`, rediscover the target window, and perform
  the current state/action sequence in the same fresh `node_repl.js` exec. Do
  not reuse a cached `globalThis.sky` client or other Computer Use bindings from
  an earlier exec; cross-exec reuse can fail with
  `node_repl exec context not found`.

## Commit & Pull Request Guidelines
- Follow the existing Conventional Commit pattern (`feat:`, `refactor:`, `chore:`) and keep subject lines within 72 characters.
- Reference related issues, list manual test steps (server, client, GUI), and attach screenshots for UI-visible changes.
- Call out model or config migrations in the PR description so maintainers can refresh packaged assets.

## Configuration & Security Notes
- Never commit API tokens, personal audio, or `.env` files; document required variables instead.
- Record every change to `config.toml` defaults and provide migration snippets to keep packaged binaries in sync with source.

## Tooling & Shell Usage
- Use `rg`/`rg --files` for searches; fall back only if unavailable.
- Use the `apply_patch` to edit files
- Avoid PowerShell-specific commands.
- When working inside WSL2, always use the Windows host toolchain for this
  repository (for example, `.venv/Scripts/python.exe`, `ruff.exe`, and
  `ty.exe`). Never run the Linux `uv` against the repository's Windows virtual
  environment.
