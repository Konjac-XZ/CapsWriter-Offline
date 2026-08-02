import yaml

from src.provider.provider_config import ProviderManager


def test_qwen_audio_provider_display_names():
    manager = ProviderManager()

    legacy = manager.get_provider("alibabacloud")
    current = manager.get_provider("qwen_audio_3")

    assert legacy is not None
    assert (legacy.name, legacy.type) == ("Qwen Audio Legacy", "qwen-audio-legacy")
    assert current is not None
    assert (current.name, current.type) == ("阿里百炼", "qwen-audio")


def test_provider_names_and_hidden_configs():
    manager = ProviderManager()

    visible = {provider["id"]: provider for provider in manager.list_providers()}
    all_providers = {
        provider["id"]: provider
        for provider in manager.list_providers(include_hidden=True)
    }

    assert visible["openrouter"]["name"] == "Google Chirp"
    assert visible["openrouter_openai"]["name"] == "GPT-4o Transcribe"
    assert visible["gemini"]["name"] == "Google Gemini"
    assert set(visible) == {
        "alibabacloud",
        "gemini",
        "openrouter",
        "openrouter_openai",
        "qwen_audio_3",
    }
    assert {
        provider_id
        for provider_id, provider in all_providers.items()
        if provider["hidden"]
    } == {
        "elevenlabs",
        "haomiao",
        "qianduoduo",
        "qianduoduo_cheap",
        "replicate",
        "soniox",
        "xiaomi",
        "yunwu",
    }


def test_update_provider_setting_persists_yaml_and_memory(tmp_path):
    config_dir = tmp_path / "providers"
    config_dir.mkdir()
    provider_file = config_dir / "alibabacloud.yaml"
    provider_file.write_text(
        yaml.safe_dump(
            {
                "name": "阿里百炼",
                "type": "dashscope",
                "description": "Qwen Audio Legacy test provider",
                "enabled": True,
                "settings": {
                    "model": "qwen3-asr-flash",
                    "realtime": True,
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    manager = ProviderManager(config_dir=config_dir)

    assert manager.update_provider_setting("alibabacloud", "realtime", False) is True

    provider = manager.get_provider("alibabacloud")
    assert provider is not None
    assert provider.settings["realtime"] is False

    data = yaml.safe_load(provider_file.read_text(encoding="utf-8"))
    assert data["settings"]["realtime"] is False
