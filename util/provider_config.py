"""
Provider configuration management system.
Handles dynamic loading and switching between transcription providers.
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass


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

        # Clear old provider settings
        self.clear_env_settings()

        # Set new provider settings
        os.environ["TRANSCRIBE_PROVIDER"] = provider.type
        os.environ["TRANSCRIBE_PROMPT"] = self.get_provider_prompt()

        if provider.type == "openai":
            os.environ["OPENAI_API_KEY"] = provider.settings.get("api_key", "")
            os.environ["OPENAI_BASE_URL"] = provider.settings.get("base_url", "")
            os.environ["TRANSCRIBE_MODEL"] = provider.settings.get("model", "")
            os.environ["TRANSCRIBE_TEMPERATURE"] = str(provider.settings.get("temperature", 0.2))
            os.environ["OPENAI_TRANSCRIBE_STREAM"] = str(provider.settings.get("stream", False))
            os.environ["OPENAI_TRANSCRIBE_LANGUAGE"] = provider.settings.get("language", "zh")
            os.environ["OPENAI_TRANSCRIBE_FORMAT"] = provider.settings.get("response_format", "text")

        elif provider.type == "replicate":
            os.environ["REPLICATE_API_TOKEN"] = provider.settings.get("api_token", "")
            os.environ["OPENAI_TRANSCRIBE_LANGUAGE"] = provider.settings.get("language", "zh")
            os.environ["TRANSCRIBE_TEMPERATURE"] = str(provider.settings.get("temperature", 0.2))
            os.environ["OPENAI_TRANSCRIBE_STREAM"] = str(provider.settings.get("stream", False))

        elif provider.type == "elevenlabs":
            os.environ["ELEVENLABS_API_KEY"] = provider.settings.get("api_key", "")
            os.environ["ELEVENLABS_LANGUAGE_CODE"] = provider.settings.get("language_code", "zh")

        elif provider.type == "soniox":
            os.environ["SONIOX_API_KEY"] = provider.settings.get("api_key", "")
            os.environ["SONIOX_MODEL"] = provider.settings.get("model", "stt-async-preview-v1")
            os.environ["SONIOX_LANGUAGE_HINTS"] = provider.settings.get("language_hints", "zh, en")
    
    def get_provider(self, provider_id: str) -> Optional[ProviderConfig]:
        """Get provider configuration by ID."""
        return self.providers.get(provider_id)
    
    def get_active_provider(self) -> Optional[ProviderConfig]:
        """Get the currently active provider configuration."""
        if self.active_provider:
            return self.providers.get(self.active_provider)
        return None
    
    def set_active_provider(self, provider_id: str) -> bool:
        """Set the active provider and update environment variables."""
        if provider_id not in self.providers:
            return False
        
        provider = self.providers[provider_id]
        
        # Clear old provider settings
        self.clear_env_settings()
        
        # Set new provider settings
        os.environ["TRANSCRIBE_PROVIDER"] = provider.type
        os.environ["TRANSCRIBE_PROMPT"] = self.get_provider_prompt()
        
        if provider.type == "openai":
            os.environ["OPENAI_API_KEY"] = provider.settings.get("api_key", "")
            os.environ["OPENAI_BASE_URL"] = provider.settings.get("base_url", "")
            os.environ["TRANSCRIBE_MODEL"] = provider.settings.get("model", "")
            os.environ["TRANSCRIBE_TEMPERATURE"] = str(provider.settings.get("temperature", 0.2))
            os.environ["OPENAI_TRANSCRIBE_STREAM"] = str(provider.settings.get("stream", False))
            os.environ["OPENAI_TRANSCRIBE_LANGUAGE"] = provider.settings.get("language", "zh")
            os.environ["OPENAI_TRANSCRIBE_FORMAT"] = provider.settings.get("response_format", "text")
            
        elif provider.type == "replicate":
            os.environ["REPLICATE_API_TOKEN"] = provider.settings.get("api_token", "")
            os.environ["OPENAI_TRANSCRIBE_LANGUAGE"] = provider.settings.get("language", "zh")
            os.environ["TRANSCRIBE_TEMPERATURE"] = str(provider.settings.get("temperature", 0.2))
            os.environ["OPENAI_TRANSCRIBE_STREAM"] = str(provider.settings.get("stream", False))
            
        elif provider.type == "elevenlabs":
            os.environ["ELEVENLABS_API_KEY"] = provider.settings.get("api_key", "")
            os.environ["ELEVENLABS_LANGUAGE_CODE"] = provider.settings.get("language_code", "zh")
            
        elif provider.type == "soniox":
            os.environ["SONIOX_API_KEY"] = provider.settings.get("api_key", "")
            os.environ["SONIOX_MODEL"] = provider.settings.get("model", "stt-async-preview-v1")
            os.environ["SONIOX_LANGUAGE_HINTS"] = provider.settings.get("language_hints", "zh, en")
        
        # Update enabled status in configs
        for pid, p in self.providers.items():
            p.enabled = (pid == provider_id)
        
        self.active_provider = provider_id
        self.save_provider_states()
        return True
    
    def clear_env_settings(self) -> None:
        """Clear environment variables for all providers."""
        env_vars = [
            "TRANSCRIBE_PROMPT", "OPENAI_API_KEY", "OPENAI_BASE_URL", "TRANSCRIBE_MODEL",
            "TRANSCRIBE_TEMPERATURE", "OPENAI_TRANSCRIBE_STREAM",
            "OPENAI_TRANSCRIBE_LANGUAGE", "OPENAI_TRANSCRIBE_FORMAT",
            "REPLICATE_API_TOKEN", "ELEVENLABS_API_KEY", "ELEVENLABS_LANGUAGE_CODE",
            "SONIOX_API_KEY", "SONIOX_MODEL", "SONIOX_LANGUAGE_HINTS"
        ]
        for var in env_vars:
            os.environ.pop(var, None)
    
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
        if self.active_provider:
            provider = self.providers.get(self.active_provider)
            if provider:
                # First check for custom prompt in provider settings
                custom_prompt = provider.settings.get("prompt")
                if custom_prompt:
                    return custom_prompt

                # Then check for prompt preset reference
                prompt_preset = provider.settings.get("prompt_preset")
                if prompt_preset:
                    preset_prompt = self.get_prompt_preset(prompt_preset)
                    if preset_prompt:
                        return preset_prompt

        # Fallback to environment variable or empty string
        return os.getenv("TRANSCRIBE_PROMPT", "")

    def set_provider_prompt(self, prompt: str) -> None:
        """Set transcription prompt in environment."""
        os.environ["TRANSCRIBE_PROMPT"] = prompt

    def get_prompt_preset(self, preset_name: str) -> Optional[str]:
        """Get a prompt preset by name from the prompts configuration."""
        prompts_file = self.config_dir.parent / "prompts.yaml"
        if not prompts_file.exists():
            return None

        try:
            with open(prompts_file, 'r', encoding='utf-8') as f:
                prompts_data = yaml.safe_load(f)

            presets = prompts_data.get("presets", {})
            return presets.get(preset_name, {}).get("text")
        except Exception as e:
            print(f"Error loading prompt preset {preset_name}: {e}")
            return None

    def list_prompt_presets(self) -> Dict[str, Dict[str, str]]:
        """List all available prompt presets."""
        prompts_file = self.config_dir.parent / "prompts.yaml"
        if not prompts_file.exists():
            return {}

        try:
            with open(prompts_file, 'r', encoding='utf-8') as f:
                prompts_data = yaml.safe_load(f)

            return prompts_data.get("presets", {})
        except Exception as e:
            print(f"Error loading prompt presets: {e}")
            return {}

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


# Global provider manager instance
provider_manager = ProviderManager()