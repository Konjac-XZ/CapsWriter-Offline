import yaml

from src.provider.provider_config import ProviderManager


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
