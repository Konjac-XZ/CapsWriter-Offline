"""
Provider configuration management system.
Handles dynamic loading and switching between transcription providers.
"""

import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.provider.domain import (
    InputMode,
    ModeConfig,
    ModelConfig,
    ModelRef,
    ProviderConfig,
    ResolvedModel,
)


class PromptManager:
    """Single-source manager for reusable prompts.

    Loads config/prompts.yaml once and provides:
    - Preset text lookup (with alias support)
    - Default preset resolution
    - Listing helpers
    """

    def __init__(self, config_root: Optional[Path] = None) -> None:
        # src/provider/ → src/ → project root → config/
        self.config_root = config_root or Path(__file__).parent.parent.parent / "config"
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


class ProviderManager:
    """Load a provider/model catalog and persist the selected model separately."""

    def __init__(
        self,
        config_dir: Optional[Path] = None,
        state_path: Optional[Path] = None,
    ):
        # src/provider/ → src/ → project root → config/providers/
        self.config_dir = (
            config_dir or Path(__file__).parent.parent.parent / "config" / "providers"
        )
        self.state_path = (
            state_path or self.config_dir.parent / "transcription_state.yaml"
        )
        self.providers: Dict[str, ProviderConfig] = {}
        self.active_provider: Optional[str] = None
        self.active_model: Optional[ModelRef] = None
        self._mode_preferences: dict[str, InputMode] = {}
        self.load_providers()

    def load_providers(self) -> None:
        """Load versioned providers without rewriting legacy files."""
        if not self.config_dir.exists():
            self.providers.clear()
            self.active_provider = None
            self.active_model = None
            return

        self.providers.clear()
        enabled_providers: list[tuple[str, float]] = []

        for yaml_file in sorted(self.config_dir.glob("*.yaml")):
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                provider_id = yaml_file.stem
                provider = self._parse_provider(provider_id, data)

                self.providers[provider_id] = provider

                # Track enabled providers
                if provider.enabled:
                    enabled_providers.append((provider_id, yaml_file.stat().st_mtime))

            except Exception as e:
                print(f"Error loading provider config {yaml_file}: {e}")

        enabled_providers.sort(key=lambda item: item[1], reverse=True)
        self._load_state()
        if self.active_model not in set(self.iter_model_refs()):
            self.active_model = self._legacy_or_first_model(enabled_providers)
        self.active_provider = (
            self.active_model.provider_id if self.active_model is not None else None
        )
        for provider_id, provider in self.providers.items():
            provider.enabled = provider_id == self.active_provider

    def _parse_provider(self, provider_id: str, data: dict[str, Any]) -> ProviderConfig:
        settings = dict(data.get("settings") or {})
        raw_models = data.get("models")
        if isinstance(raw_models, dict) and raw_models:
            legacy = False
            models = self._parse_models(raw_models)
        else:
            legacy = True
            models = self._parse_legacy_model(settings)
        if not models:
            raise ValueError("provider must declare at least one model")
        return ProviderConfig(
            id=provider_id,
            name=str(data["name"]),
            type=str(data["type"]).strip().lower(),
            description=str(data.get("description") or ""),
            settings=settings,
            models=models,
            enabled=bool(data.get("enabled", False)),
            hidden=bool(data.get("hidden", False)),
            schema_version=int(data.get("schema_version", 1 if legacy else 2)),
            legacy=legacy,
        )

    def _parse_models(self, raw_models: dict[str, Any]) -> dict[str, ModelConfig]:
        models: dict[str, ModelConfig] = {}
        for model_id, raw in raw_models.items():
            if not isinstance(raw, dict):
                raise ValueError(f"model {model_id!r} must be a mapping")
            model_settings = dict(raw.get("settings") or {})
            upstream = str(
                raw.get("upstream_model") or model_settings.get("model") or model_id
            ).strip()
            raw_modes = raw.get("modes") or {InputMode.FILE_UPLOAD.value: {}}
            modes: dict[InputMode, ModeConfig] = {}
            if isinstance(raw_modes, list):
                raw_modes = {str(mode): {} for mode in raw_modes}
            if not isinstance(raw_modes, dict):
                raise ValueError(f"model {model_id!r} modes must be a mapping or list")
            for mode_name, mode_data in raw_modes.items():
                mode = InputMode(str(mode_name))
                if mode_data is None:
                    mode_data = {}
                if not isinstance(mode_data, dict):
                    raise ValueError(f"model {model_id!r} mode {mode.value} is invalid")
                mode_settings = dict(mode_data.get("settings") or mode_data)
                modes[mode] = ModeConfig(mode_settings)
            models[str(model_id)] = ModelConfig(
                id=str(model_id),
                name=str(raw.get("name") or upstream),
                upstream_model=upstream,
                settings=model_settings,
                modes=modes,
                incremental_output=bool(raw.get("incremental_output", False)),
                default_mode=InputMode(
                    str(raw.get("default_mode", InputMode.FILE_UPLOAD.value))
                ),
            )
        return models

    def _parse_legacy_model(self, settings: dict[str, Any]) -> dict[str, ModelConfig]:
        upstream = str(settings.get("model") or "default")
        modes: dict[InputMode, ModeConfig] = {
            InputMode.FILE_UPLOAD: ModeConfig({"model": upstream, "realtime": False})
        }
        if "realtime" in settings:
            live_model = str(settings.get("realtime_model") or upstream)
            modes[InputMode.LIVE_AUDIO] = ModeConfig(
                {"model": live_model, "realtime": True}
            )
        return {
            "default": ModelConfig(
                id="default",
                name=upstream,
                upstream_model=upstream,
                settings={},
                modes=modes,
                incremental_output=bool(settings.get("stream", False)),
                default_mode=(
                    InputMode.LIVE_AUDIO
                    if bool(settings.get("realtime", False))
                    else InputMode.FILE_UPLOAD
                ),
            )
        }

    def _load_state(self) -> None:
        self.active_model = None
        self._mode_preferences.clear()
        if not self.state_path.exists():
            return
        try:
            data = yaml.safe_load(self.state_path.read_text(encoding="utf-8")) or {}
            active = data.get("active_model") or {}
            if (
                isinstance(active, dict)
                and active.get("provider_id")
                and active.get("model_id")
            ):
                self.active_model = ModelRef(
                    str(active["provider_id"]), str(active["model_id"])
                )
            for key, value in (data.get("model_modes") or {}).items():
                self._mode_preferences[str(key)] = InputMode(str(value))
        except Exception as exc:
            print(f"Error loading transcription state {self.state_path}: {exc}")

    def _legacy_or_first_model(
        self, enabled_providers: list[tuple[str, float]]
    ) -> Optional[ModelRef]:
        for provider_id, _mtime in enabled_providers:
            provider = self.providers.get(provider_id)
            if provider and not provider.hidden and provider.models:
                return ModelRef(provider_id, next(iter(provider.models)))
        return next(self.iter_model_refs(), None)

    def iter_model_refs(self, include_hidden: bool = False):
        for provider_id, provider in self.providers.items():
            if provider.hidden and not include_hidden:
                continue
            for model_id in provider.models:
                yield ModelRef(provider_id, model_id)

    def list_models(self, include_hidden: bool = False) -> List[Dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for ref in self.iter_model_refs(include_hidden=include_hidden):
            provider = self.providers[ref.provider_id]
            model = provider.models[ref.model_id]
            result.append(
                {
                    "ref": ref,
                    "provider_id": ref.provider_id,
                    "model_id": ref.model_id,
                    "provider_name": provider.name,
                    "name": model.name,
                    "label": f"{model.name} · {provider.name}",
                    "input_modes": model.input_modes,
                    "incremental_output": model.incremental_output,
                    "active": ref == self.active_model,
                }
            )
        return result

    def get_model(self, ref: ModelRef) -> Optional[ModelConfig]:
        provider = self.providers.get(ref.provider_id)
        return provider.models.get(ref.model_id) if provider else None

    def get_active_model_ref(self) -> Optional[ModelRef]:
        return self.active_model

    def get_model_mode(self, ref: ModelRef) -> InputMode:
        model = self.get_model(ref)
        if model is None:
            raise KeyError(ref.key)
        preferred = self._mode_preferences.get(ref.key)
        if preferred in model.input_modes:
            return preferred
        if model.default_mode in model.input_modes:
            return model.default_mode
        if InputMode.FILE_UPLOAD in model.input_modes:
            return InputMode.FILE_UPLOAD
        return next(iter(model.input_modes))

    def resolve_model(
        self, ref: Optional[ModelRef] = None, mode: Optional[InputMode] = None
    ) -> ResolvedModel:
        ref = ref or self.active_model
        if ref is None:
            raise RuntimeError("No transcription model is configured")
        provider = self.providers.get(ref.provider_id)
        model = self.get_model(ref)
        if provider is None or model is None:
            raise KeyError(f"Unknown transcription model: {ref.key}")
        from src.transcribe.providers import make_provider

        adapter = make_provider(provider.type)
        unsupported = model.input_modes - adapter.supported_input_modes()
        if unsupported:
            names = ", ".join(sorted(item.value for item in unsupported))
            raise ValueError(
                f"Model {ref.key} declares modes unsupported by {provider.type}: {names}"
            )
        resolved = ResolvedModel.create(
            provider, model, mode or self.get_model_mode(ref)
        )
        if resolved.adapter_type != adapter.name():
            resolved = replace(resolved, adapter_type=adapter.name())
        return resolved

    def get_active_model(self) -> Optional[ResolvedModel]:
        try:
            return self.resolve_model()
        except (KeyError, RuntimeError, ValueError):
            return None

    def set_active_model(self, ref: ModelRef) -> bool:
        if self.get_model(ref) is None:
            return False
        previous_model = self.active_model
        previous_provider = self.active_provider
        previous_enabled = {
            provider_id: provider.enabled
            for provider_id, provider in self.providers.items()
        }
        self.active_model = ref
        self.active_provider = ref.provider_id
        for provider_id, provider in self.providers.items():
            provider.enabled = provider_id == ref.provider_id
        if self._save_state():
            return True
        self.active_model = previous_model
        self.active_provider = previous_provider
        for provider_id, enabled in previous_enabled.items():
            self.providers[provider_id].enabled = enabled
        return False

    def set_model_mode(self, ref: ModelRef, mode: InputMode) -> bool:
        model = self.get_model(ref)
        if model is None or mode not in model.input_modes:
            return False
        previous = self._mode_preferences.get(ref.key)
        self._mode_preferences[ref.key] = mode
        if self._save_state():
            return True
        if previous is None:
            self._mode_preferences.pop(ref.key, None)
        else:
            self._mode_preferences[ref.key] = previous
        return False

    def _save_state(self) -> bool:
        if self.active_model is None:
            return False
        data = {
            "schema_version": 1,
            "active_model": {
                "provider_id": self.active_model.provider_id,
                "model_id": self.active_model.model_id,
            },
            "model_modes": {
                key: mode.value for key, mode in sorted(self._mode_preferences.items())
            },
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_dump(self.state_path, data)
            return True
        except Exception as exc:
            print(f"Error saving transcription state {self.state_path}: {exc}")
            return False

    @staticmethod
    def _atomic_dump(path: Path, data: dict[str, Any]) -> None:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
        ) as handle:
            yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)
            temporary = Path(handle.name)
        os.replace(temporary, path)

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
        model = self.get_active_model()
        return model.adapter_type if model else None

    def get_active_settings(self) -> Dict[str, Any]:
        model = self.get_active_model()
        return dict(model.settings) if model else {}

    def set_active_provider(self, provider_id: str) -> bool:
        """Compatibility facade selecting the provider's first model."""
        provider = self.providers.get(provider_id)
        if provider is None or not provider.models:
            return False
        return self.set_active_model(ModelRef(provider_id, next(iter(provider.models))))

    def clear_env_settings(self) -> None:
        """No-op in YAML-first mode; kept for backward compatibility."""
        return

    def save_provider_states(self) -> None:
        """Compatibility facade; active selection now lives in the state file."""
        self._save_state()

    def list_providers(self, include_hidden: bool = False) -> List[Dict[str, Any]]:
        """List selectable providers, optionally including hidden configurations."""
        return [
            {
                "id": provider_id,
                "name": provider.name,
                "type": provider.type,
                "description": provider.description,
                "enabled": provider.enabled,
                "hidden": provider.hidden,
                "models": len(provider.models),
            }
            for provider_id, provider in self.providers.items()
            if include_hidden or not provider.hidden
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
                from_text = (
                    prompt_manager.get_text(preset_name) if preset_name else None
                )
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

    def update_provider_prompt(
        self,
        provider_id: str,
        prompt: Optional[str] = None,
        prompt_preset: Optional[str] = None,
    ) -> bool:
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
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)

                # Update the prompt settings
                if prompt is not None:
                    data["settings"]["prompt"] = prompt
                    data["settings"].pop("prompt_preset", None)
                elif prompt_preset is not None:
                    data["settings"]["prompt_preset"] = prompt_preset
                    data["settings"].pop("prompt", None)
                else:
                    # Clear both if neither provided
                    data["settings"].pop("prompt", None)
                    data["settings"].pop("prompt_preset", None)

                with open(yaml_file, "w", encoding="utf-8") as f:
                    yaml.safe_dump(
                        data, f, default_flow_style=False, allow_unicode=True
                    )

                return True

            except Exception as e:
                print(f"Error updating provider prompt {yaml_file}: {e}")
                return False

        return False

    def update_provider_model(self, provider_id: str, model: str) -> bool:
        """Legacy-only compatibility; v2 model catalogs are declarative."""
        provider = self.providers.get(provider_id)
        if provider is None or not provider.legacy:
            return False
        return self.update_provider_setting(provider_id, "model", model)

    def update_provider_setting(self, provider_id: str, key: str, value: Any) -> bool:
        """Update one provider setting and persist it to the provider YAML."""
        if provider_id not in self.providers:
            return False

        key = str(key or "").strip()
        if not key:
            return False

        provider = self.providers[provider_id]
        if provider.settings is None:
            provider.settings = {}
        provider.settings[key] = value

        yaml_file = self.config_dir / f"{provider_id}.yaml"
        if yaml_file.exists():
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

                settings = data.setdefault("settings", {})
                if not isinstance(settings, dict):
                    settings = {}
                    data["settings"] = settings
                settings[key] = value

                with open(yaml_file, "w", encoding="utf-8") as f:
                    yaml.safe_dump(
                        data, f, default_flow_style=False, allow_unicode=True
                    )

                return True

            except Exception as e:
                print(f"Error updating provider setting {yaml_file}: {e}")
                return False

        return False

    def update_prompt_preset_text(self, preset_name: str, text: str) -> bool:
        """Update the text for a named prompt preset in prompts.yaml."""
        return prompt_manager.update_preset_text(preset_name, text)


# Global provider manager instance
provider_manager = ProviderManager()
