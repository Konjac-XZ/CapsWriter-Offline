from tomlkit import dump


def write_toml(config, config_path):
    with open(config_path, "w", encoding="utf-8") as f:
        dump(config, f)
