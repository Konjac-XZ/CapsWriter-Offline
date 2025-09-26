# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Important**: This is a personal fork that has undergone sweeping architectural changes from the original CapsWriter-Offline project. While the original was a complete offline server-client speech recognition system, this fork has evolved into a **cloud-based transcription client** that connects directly to proprietary cloud models.

### Current Architecture (Personal Fork)
- **Client-only system**: No local server required
- **Cloud transcription**: Connects directly to proprietary cloud models via various APIs
- **Simplified feature set**: Focused on core speech-to-text functionality
- **Legacy code**: Much of the visible codebase is unused/legacy from the original offline implementation

### What's Actually Used
- **Client GUI** (`start_client_gui.py`): Main application interface
- **Provider System**: Dynamic switching between cloud transcription services
- **Configuration**: Provider management and API credentials
- **Audio Input**: Microphone capture and keyboard shortcuts (CapsLock)

### What's Legacy/Unused
- **Server components** (`core_server.py`, `start_server_gui.py`): Original offline LLM server (not used)
- **Local AI models** (models/ directory): Offline speech recognition models (not used)  
- **Translation features**: Simultaneous translation functionality (dropped)
- **WebSocket communication**: Original server-client communication (not used)
- **Most util/ modules**: Many utilities for offline processing (not used)

## Active Components

### Core Functionality
- **Cloud Transcription**: Direct API calls to proprietary cloud models (OpenAI-compatible, Replicate, ElevenLabs, Soniox)
- **Provider Management**: Dynamic switching between transcription services without .env editing
- **Audio Input**: Microphone capture triggered by CapsLock keyboard shortcut
- **Text Output**: Real-time transcription results displayed in GUI and auto-typed

### Configuration System
- **Provider Configs**: `config/providers/*.yaml` - Individual cloud service configurations
- **Dynamic Switching**: GUI-based and CLI provider management
- **API Credentials**: Secure storage of API keys and endpoints per provider

## Development Commands

### Running the Application
```bash
# Main client application (only component actually used)
python start_client_gui.py

# Configuration GUI for provider management
python edit_config_gui.py
```

### Legacy Commands (Not Used)
```bash
# These are from the original offline system - not used in current fork:
# .\runtime\python.exe .\core_server.py      # Original offline server
# .\runtime\python.exe .\core_client.py      # Original terminal client  
# python start_server_gui.py                 # Original server GUI
```

### Testing
Test the current cloud-based functionality:
- Use CapsLock key for voice input testing with active cloud provider
- Use GUI provider switcher to test different transcription services
- Test audio file transcription (if still functional in current fork)

### Legacy Testing (Not Applicable)
```bash
# These were for the original offline system:
# - Drag audio/video files to client GUI for transcription testing
# - Use Shift+CapsLock combinations for translation testing (dropped feature)
```

### Provider Management
```bash
# List all available transcription providers
python provider_switch.py --list

# Switch to a specific provider
python provider_switch.py --switch qianduoduo

# Show current active provider
python provider_switch.py --current

# Reload provider configurations
python provider_switch.py --reload
```

### Dependencies
Active dependencies (for current cloud-based fork):
- `requirements-client.txt` - Client GUI and cloud API dependencies
- `requirements-editconfiggui.txt` - Configuration GUI dependencies

Legacy dependencies (not used):
- `requirements-server.txt` - Original offline server dependencies (not used)

## Configuration System

### Active Configuration
- **Provider Configs**: `config/providers/*.yaml` - Cloud service API credentials and settings
- **Provider Management**: `util/provider_config.py` - Handles dynamic provider switching
- **Environment Variables**: Automatically set by provider manager when switching

### Legacy Configuration (Mostly Unused)
- `config.toml` - Original comprehensive configuration (most sections not used)
- Configuration classes in `util/config.py` - Original server/client settings (mostly unused)

## Important File Structure

### Active Files (Actually Used)
- `start_client_gui.py` - Main application entry point
- `config/providers/*.yaml` - Cloud service configurations  
- `util/provider_config.py` - Provider management system
- `util/transcribe_provider.py` - Cloud API integration
- `util/edit_config_gui/provider_config_page.py` - Provider GUI
- `provider_switch.py` - CLI provider management tool

### Legacy Files (Large Codebase, Mostly Unused)
- `core_server.py`, `start_server_gui.py` - Original offline server
- `core_client.py` - Original terminal client
- `models/` - Original offline AI models directory
- `util/server_*` - Server-side utilities (not used)
- `util/client_translate_*` - Translation features (dropped)
- Most other `util/` modules - Original offline functionality

## Development Notes

### Current Architecture Focus
- **Cloud-first**: All transcription happens via external APIs
- **Simplified**: Core functionality is voice input → cloud API → text output
- **Provider-agnostic**: Can switch between different cloud services dynamically
- **Personal use**: Heavily customized fork for single-user scenarios

### Legacy Code Considerations
- **Large unused codebase**: Much of the visible code is from the original offline system
- **Don't be confused**: Server components, model files, and many utilities are not used
- **Focus on active files**: When making changes, focus on the "Active Files" listed above
- **Backwards compatibility**: Some legacy code remains to avoid breaking imports

## Dynamic Provider System

### Provider Configuration
The system now supports dynamic switching between transcription providers without modifying .env files:

- **Provider Configs**: `config/providers/*.yaml` - Individual provider configurations
- **Provider Manager**: `util/provider_config.py` - Handles provider switching and environment setup
- **GUI Integration**: Added to `edit_config_gui.py` for easy provider switching
- **CLI Tool**: `provider_switch.py` for command-line provider management

### Supported Providers
- **OpenAI-compatible**: QianDuoDuo, Local New-API, Haomiao, Azapi
- **Replicate**: Replicate AI transcription service
- **ElevenLabs**: ElevenLabs speech-to-text API
- **Soniox**: Soniox speech recognition API

### Provider YAML Structure
```yaml
name: "Provider Name"
type: "openai|replicate|elevenlabs|soniox"
description: "Provider description"
settings:
  api_key: "your-api-key"
  base_url: "https://api.example.com"
  # provider-specific settings...
enabled: true|false
```

### Usage
1. Use GUI config editor to switch providers visually
2. Use CLI tool for scripting and automation
3. Providers automatically set environment variables on switch
4. Original .env fallback maintained for compatibility
- Do not attempt to run this program—you will run into dependency issues and won’t see anything worthwhile.