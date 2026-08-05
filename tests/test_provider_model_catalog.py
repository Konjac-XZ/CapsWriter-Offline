from pathlib import Path

import yaml

from src.provider.domain import InputMode, ModelRef
from src.provider.migrate_config import migrate_directory, migrate_provider_data
from src.provider.provider_config import ProviderManager


def _write_provider(path: Path, data: dict) -> None:
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _v2_provider(name: str, provider_type: str, models: dict, **settings):
    return {
        "schema_version": 2,
        "name": name,
        "type": provider_type,
        "description": "test provider",
        "settings": settings,
        "models": models,
    }


def test_catalog_uses_composite_identity_for_duplicate_model_names(tmp_path):
    config_dir = tmp_path / "providers"
    config_dir.mkdir()
    model = {
        "default": {
            "name": "Shared ASR",
            "upstream_model": "shared-asr",
            "modes": ["file_upload"],
        }
    }
    _write_provider(config_dir / "alpha.yaml", _v2_provider("Alpha", "openai", model))
    _write_provider(config_dir / "beta.yaml", _v2_provider("Beta", "openai", model))

    manager = ProviderManager(config_dir, state_path=tmp_path / "state.yaml")

    listed = manager.list_models()
    assert [item["ref"] for item in listed] == [
        ModelRef("alpha", "default"),
        ModelRef("beta", "default"),
    ]
    assert [item["label"] for item in listed] == [
        "Shared ASR · Alpha",
        "Shared ASR · Beta",
    ]


def test_resolved_model_layers_provider_model_and_mode_settings(tmp_path):
    config_dir = tmp_path / "providers"
    config_dir.mkdir()
    _write_provider(
        config_dir / "qwen.yaml",
        _v2_provider(
            "Qwen",
            "qwen-audio",
            {
                "flash": {
                    "name": "Qwen Flash",
                    "upstream_model": "file-model",
                    "settings": {"temperature": 0.1, "language": "zh"},
                    "default_mode": "live_audio",
                    "modes": {
                        "file_upload": {"settings": {"model": "file-model"}},
                        "live_audio": {
                            "settings": {"model": "live-model", "temperature": 0.0}
                        },
                    },
                }
            },
            api_key="placeholder",
            temperature=0.5,
        ),
    )
    manager = ProviderManager(config_dir, state_path=tmp_path / "state.yaml")

    resolved = manager.resolve_model()

    assert resolved.ref == ModelRef("qwen", "flash")
    assert resolved.input_mode is InputMode.LIVE_AUDIO
    assert resolved.upstream_model == "live-model"
    assert resolved.settings["api_key"] == "placeholder"
    assert resolved.settings["language"] == "zh"
    assert resolved.settings["temperature"] == 0.0


def test_model_mode_preferences_are_persisted_per_composite_ref(tmp_path):
    config_dir = tmp_path / "providers"
    config_dir.mkdir()
    modes = {"file_upload": {}, "live_audio": {}}
    _write_provider(
        config_dir / "qwen.yaml",
        _v2_provider(
            "Qwen",
            "qwen-audio",
            {
                "one": {"upstream_model": "one", "modes": modes},
                "two": {"upstream_model": "two", "modes": modes},
            },
        ),
    )
    state_path = tmp_path / "transcription_state.yaml"
    manager = ProviderManager(config_dir, state_path=state_path)
    second = ModelRef("qwen", "two")

    assert manager.set_active_model(second)
    assert manager.set_model_mode(second, InputMode.LIVE_AUDIO)

    reloaded = ProviderManager(config_dir, state_path=state_path)
    assert reloaded.get_active_model_ref() == second
    assert reloaded.get_model_mode(second) is InputMode.LIVE_AUDIO


def test_legacy_stream_and_realtime_are_independent_capabilities(tmp_path):
    config_dir = tmp_path / "providers"
    config_dir.mkdir()
    _write_provider(
        config_dir / "qwen.yaml",
        {
            "name": "Qwen",
            "type": "qwen-audio",
            "description": "legacy",
            "enabled": True,
            "settings": {
                "model": "file-model",
                "realtime": False,
                "realtime_model": "live-model",
                "stream": True,
            },
        },
    )

    manager = ProviderManager(config_dir, state_path=tmp_path / "state.yaml")
    model = manager.get_model(ModelRef("qwen", "default"))

    assert model is not None
    assert model.input_modes == {InputMode.FILE_UPLOAD, InputMode.LIVE_AUDIO}
    assert model.incremental_output is True
    assert manager.get_model_mode(ModelRef("qwen", "default")) is InputMode.FILE_UPLOAD


def test_migration_dry_run_backup_write_and_idempotence(tmp_path):
    config_dir = tmp_path / "providers"
    config_dir.mkdir()
    path = config_dir / "qwen.yaml"
    legacy = {
        "name": "Qwen",
        "type": "qwen-audio",
        "description": "legacy",
        "enabled": True,
        "settings": {
            "api_key": "placeholder",
            "model": "file-model",
            "realtime": True,
            "realtime_model": "live-model",
            "stream": False,
        },
    }
    _write_provider(path, legacy)

    assert migrate_directory(config_dir) == [path]
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == legacy

    assert migrate_directory(config_dir, write=True) == [path]
    backups = list(config_dir.glob("qwen.yaml.*.bak"))
    assert len(backups) == 1
    migrated = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["models"]["default"]["default_mode"] == "live_audio"
    assert migrated["models"]["default"]["modes"]["live_audio"]["settings"][
        "model"
    ] == "live-model"
    assert migrate_directory(config_dir, write=True) == []
    assert migrate_provider_data(migrated) == migrated
