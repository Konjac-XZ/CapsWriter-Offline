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
   # Set your provider API key (e.g., OpenAI)
   set OPENAI_API_KEY=your_key_here
   ```

4. Run the GUI:
   ```bash
   uv run python start_client_gui.py
   ```

## Configuration

- Providers: Edit YAML files in `config/providers/`
- Client settings: Edit `config.toml`
- Use `python provider_switch.py --list` to see available providers

## Documentation

- `CLAUDE.md`: Technical architecture and component details
- `AGENTS.md`: Development workflow and agent integration

## Requirements

- Python 3.11+
- Windows OS
- uv package manager
