"""
Provider configuration management system.
Handles dynamic loading and switching between transcription providers.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass


class PromptManager:
    """Single-source manager for reusable prompts.

    Loads config/prompts.yaml once and provides:
    - Preset text lookup (with alias support)
    - Default preset resolution
    - Listing helpers
    """

    def __init__(self, config_root: Optional[Path] = None) -> None:
        self.config_root = config_root or Path(__file__).parent.parent / "config"
        self._prompts_path = self.config_root / "prompts.yaml"
        self._cache_mtime: float | None = None
        self._data: Dict[str, Any] = {}

    def _maybe_reload(self) -> None:
        """Reload prompts.yaml if it's changed on disk."""
        path = self._prompts_path
        if not path.exists():
            self._data = {}
            self._cache_mtime = None
            return
        try:
            mtime = path.stat().st_mtime
            if self._cache_mtime is None or mtime != self._cache_mtime:
                with open(path, "r", encoding="utf-8") as f:
                    self._data = yaml.safe_load(f) or {}
                self._cache_mtime = mtime
        except Exception as e:  # pragma: no cover
            print(f"Error loading prompts from {path}: {e}")
            self._data = {}
            self._cache_mtime = None

    @property
    def data(self) -> Dict[str, Any]:
        self._maybe_reload()
        return self._data

    def _aliases(self) -> Dict[str, str]:
        return (self.data.get("aliases") or {}) if isinstance(self.data, dict) else {}

    def _presets(self) -> Dict[str, Dict[str, Any]]:
        presets = self.data.get("presets") or {}
        return presets if isinstance(presets, dict) else {}

    def resolve_name(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        name = str(name).strip()
        if not name:
            return None
        presets = self._presets()
        if name in presets:
            return name
        alias = self._aliases().get(name)
        if alias in presets:
            return alias
        # No match
        return None

    def get_text(self, name: Optional[str]) -> Optional[str]:
        key = self.resolve_name(name)
        if not key:
            return None
        preset = self._presets().get(key) or {}
        text = preset.get("text")
        if isinstance(text, str):
            return text
        return None

    def get_default_preset(self) -> Optional[str]:
        raw = self.data.get("default_preset")
        if isinstance(raw, str):
            return self.resolve_name(raw)
        return None

    def get_default_prompt_text(self) -> Optional[str]:
        default_name = self.get_default_preset()
        if default_name:
            return self.get_text(default_name)
        return None

    def list_presets(self) -> Dict[str, Dict[str, Any]]:
        return self._presets()

    def list_aliases(self) -> Dict[str, str]:
        return self._aliases()

    def update_preset_text(self, name: str, new_text: str) -> bool:
        """Persist updated text for a given preset back to prompts.yaml."""
        path = self._prompts_path
        if not path.exists():
            return False

        resolved_name = self.resolve_name(name)
        if not resolved_name:
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Error loading prompts from {path}: {e}")
            return False

        presets = data.setdefault("presets", {})
        if not isinstance(presets, dict):
            presets = {}
            data["presets"] = presets

        preset_entry = presets.get(resolved_name)
        if not isinstance(preset_entry, dict):
            preset_entry = {}
            presets[resolved_name] = preset_entry

        preset_entry["text"] = new_text

        try:
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    data,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            self._data = data
            try:
                self._cache_mtime = path.stat().st_mtime
            except Exception:
                self._cache_mtime = None
            return True
        except Exception as e:
            print(f"Error writing prompts to {path}: {e}")
            return False


# Global prompt manager instance (single source of truth for prompts)
prompt_manager = PromptManager()


@dataclass
class ProviderConfig:
    """Configuration for a single transcription provider."""
    name: str
    type: str
    description: str
    settings: Dict[str, Any]
    enabled: bool


class ProviderManager:
    """Manages transcription provider configurations."""
    
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path(__file__).parent.parent / "config" / "providers"
        self.providers: Dict[str, ProviderConfig] = {}
        self.active_provider: Optional[str] = None
        self.load_providers()
    
    def load_providers(self) -> None:
        """Load all provider configurations from YAML files."""
        if not self.config_dir.exists():
            self.config_dir.mkdir(parents=True, exist_ok=True)
            return

        self.providers.clear()
        enabled_providers = []

        for yaml_file in sorted(self.config_dir.glob("*.yaml")):
            try:
                with open(yaml_file, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)

                provider_id = yaml_file.stem
                provider = ProviderConfig(
                    name=data['name'],
                    type=data['type'],
                    description=data['description'],
                    settings=data['settings'],
                    enabled=data.get('enabled', False)
                )

                self.providers[provider_id] = provider

                # Track enabled providers
                if provider.enabled:
                    enabled_providers.append((provider_id, yaml_file.stat().st_mtime))

            except Exception as e:
                print(f"Error loading provider config {yaml_file}: {e}")

        # Set active provider: prefer the most recently modified if multiple are enabled
        if enabled_providers:
            # Sort by modification time, take the most recent
            enabled_providers.sort(key=lambda x: x[1], reverse=True)
            self.active_provider = enabled_providers[0][0]

            # If multiple providers are enabled (shouldn't happen), fix it
            if len(enabled_providers) > 1:
                print(f"Warning: Multiple providers enabled, selecting {self.active_provider}")
                # Disable all others and save the corrected state
                for pid, p in self.providers.items():
                    p.enabled = (pid == self.active_provider)
                self.save_provider_states()

            # Initialize environment variables for the active provider
            self._set_environment_for_active_provider()

    def _set_environment_for_active_provider(self) -> None:
        """Set environment variables for the currently active provider."""
        if not self.active_provider:
            return

        provider = self.providers.get(self.active_provider)
        if not provider:
            return

        # Migration note: we no longer mirror settings to environment variables.
        # Handlers should query ProviderManager/providers' settings directly.
    
    def get_provider(self, provider_id: str) -> Optional[ProviderConfig]:
        """Get provider configuration by ID."""
        return self.providers.get(provider_id)
    
    def get_active_provider(self) -> Optional[ProviderConfig]:
        """Get the currently active provider configuration."""
        if self.active_provider:
            return self.providers.get(self.active_provider)
        return None

    def get_active_provider_type(self) -> Optional[str]:
        p = self.get_active_provider()
        return p.type if p else None

    def get_active_settings(self) -> Dict[str, Any]:
        p = self.get_active_provider()
        return dict(p.settings) if p and p.settings else {}
    
    def set_active_provider(self, provider_id: str) -> bool:
        """Set the active provider and update environment variables."""
        if provider_id not in self.providers:
            return False
        
        provider = self.providers[provider_id]
        
        # We no longer export settings to env; only track active id internally
        
        # Handlers will read settings from ProviderManager instead.
        
        # Update enabled status in configs
        for pid, p in self.providers.items():
            p.enabled = (pid == provider_id)
        
        self.active_provider = provider_id
        self.save_provider_states()
        return True
    
    def clear_env_settings(self) -> None:
        """No-op in YAML-first mode; kept for backward compatibility."""
        return
    
    def save_provider_states(self) -> None:
        """Save current enabled states back to YAML files."""
        for provider_id, provider in self.providers.items():
            yaml_file = self.config_dir / f"{provider_id}.yaml"
            if yaml_file.exists():
                try:
                    with open(yaml_file, 'r', encoding='utf-8') as f:
                        data = yaml.safe_load(f)
                    
                    data['enabled'] = provider.enabled
                    
                    with open(yaml_file, 'w', encoding='utf-8') as f:
                        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
                        
                except Exception as e:
                    print(f"Error saving provider state {yaml_file}: {e}")
    
    def list_providers(self) -> List[Dict[str, Any]]:
        """List all available providers with their info."""
        return [
            {
                "id": provider_id,
                "name": provider.name,
                "type": provider.type,
                "description": provider.description,
                "enabled": provider.enabled
            }
            for provider_id, provider in self.providers.items()
        ]
    
    def get_provider_prompt(self) -> str:
        """Get transcription prompt from active provider or default."""
        # Prefer provider-specific overrides
        if self.active_provider:
            provider = self.providers.get(self.active_provider)
            if provider:
                settings = provider.settings or {}
                # Explicit inline prompt wins
                custom_prompt = settings.get("prompt")
                if isinstance(custom_prompt, str) and custom_prompt.strip():
                    return custom_prompt

                # Preset reference (with alias support)
                preset_name = settings.get("prompt_preset")
                from_text = prompt_manager.get_text(preset_name) if preset_name else None
                if from_text:
                    return from_text

        # Global default from prompts.yaml
        global_default = prompt_manager.get_default_prompt_text()
        if isinstance(global_default, str) and global_default.strip():
            return global_default

        # Back-compat: allow environment variable fallback
        return os.getenv("TRANSCRIBE_PROMPT", "")

    def set_provider_prompt(self, prompt: str) -> None:
        """Set transcription prompt in environment."""
        os.environ["TRANSCRIBE_PROMPT"] = prompt

    def get_prompt_preset(self, preset_name: str) -> Optional[str]:
        """Get a prompt preset by name (aliases supported)."""
        return prompt_manager.get_text(preset_name)

    def list_prompt_presets(self) -> Dict[str, Dict[str, str]]:
        """List all available prompt presets."""
        # Keep return shape stable (name -> {name, description, text})
        raw = prompt_manager.list_presets()
        return {k: v for k, v in raw.items()}

    def update_provider_prompt(self, provider_id: str, prompt: Optional[str] = None, prompt_preset: Optional[str] = None) -> bool:
        """Update prompt setting for a provider and save to file."""
        if provider_id not in self.providers:
            return False

        provider = self.providers[provider_id]

        # Clear both prompt settings first
        provider.settings.pop("prompt", None)
        provider.settings.pop("prompt_preset", None)

        # Set the new prompt configuration
        if prompt is not None:
            provider.settings["prompt"] = prompt
        elif prompt_preset is not None:
            provider.settings["prompt_preset"] = prompt_preset

        # If this is the active provider, update environment
        if provider.enabled:
            os.environ["TRANSCRIBE_PROMPT"] = self.get_provider_prompt()

        # Save to YAML file
        yaml_file = self.config_dir / f"{provider_id}.yaml"
        if yaml_file.exists():
            try:
                with open(yaml_file, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)

                # Update the prompt settings
                if prompt is not None:
                    data['settings']['prompt'] = prompt
                    data['settings'].pop('prompt_preset', None)
                elif prompt_preset is not None:
                    data['settings']['prompt_preset'] = prompt_preset
                    data['settings'].pop('prompt', None)
                else:
                    # Clear both if neither provided
                    data['settings'].pop('prompt', None)
                    data['settings'].pop('prompt_preset', None)

                with open(yaml_file, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)

                return True

            except Exception as e:
                print(f"Error updating provider prompt {yaml_file}: {e}")
                return False

        return False

    def update_provider_model(self, provider_id: str, model: str) -> bool:
        """Update model setting for a provider and save to file."""
        if provider_id not in self.providers:
            return False

        provider = self.providers[provider_id]
        provider.settings["model"] = model

        # If this is the active provider, update environment
        if provider.enabled:
            os.environ["TRANSCRIBE_MODEL"] = model

        # Save to YAML file
        yaml_file = self.config_dir / f"{provider_id}.yaml"
        if yaml_file.exists():
            try:
                with open(yaml_file, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)

                data['settings']['model'] = model

                with open(yaml_file, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)

                return True

            except Exception as e:
                print(f"Error updating provider model {yaml_file}: {e}")
                return False

        return False

    def update_prompt_preset_text(self, preset_name: str, text: str) -> bool:
        """Update the text for a named prompt preset in prompts.yaml."""
        return prompt_manager.update_preset_text(preset_name, text)


# Global provider manager instance
provider_manager = ProviderManager()
