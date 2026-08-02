import sys
from pathlib import Path
from typing import Annotated, Mapping

import tomllib
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

# 加载TOML配置文件
# When running from PyInstaller, look for config.toml next to the executable
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    # Running in PyInstaller bundle - config.toml is in the same directory as the .exe
    config_toml_path = Path(sys.executable).parent / "config.toml"
else:
    # Running as script - use relative path (src/infra/ → src/ → project root)
    config_toml_path = Path(__file__).parent.parent.parent / "config.toml"

with config_toml_path.open("rb") as file:
    config = tomllib.load(file)


def _path_from_toml(value: object) -> Path:
    if isinstance(value, Path):
        return value
    if isinstance(value, str):
        return Path(value)
    raise ValueError("must be a string path")


ConfigPath = Annotated[Path, BeforeValidator(_path_from_toml)]


class ClientConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, validate_assignment=True)

    speech_recognition_shortcut: str = ""
    toggle_textbox_context_shortcut: str = ""
    threshold: float = 0.0
    paste: bool = False
    restore_clipboard_after_paste: bool = False
    tsf_speech_tip_enabled: bool = False
    tsf_speech_tip_ack_timeout_ms: int = Field(default=150, ge=10)
    daily_input_log_interval: int = Field(default=1000, ge=1)
    save_audio: bool = False
    save_markdown: bool = False
    audio_name_len: int = 0
    reduce_audio_files: bool = False
    trash_punc: str = ""
    mute_other_audio: bool = False
    pause_other_audio: bool = False
    shrink_automatically_to_tray: bool = False
    only_run_once: bool = False
    only_enable_microphones_when_pressed_record_shortcut: bool = False
    play_start_music: bool = False
    start_music_path: ConfigPath = Path()
    start_music_volume: str = ""
    play_stop_music: bool = False
    stop_music_path: ConfigPath = Path()
    stop_music_volume: str = ""
    show_listening_overlay: bool = True
    check_microphone_usage_by: str = ""


def _parse_client_config(section: Mapping[str, object]) -> ClientConfig:
    return ClientConfig.model_validate(section)


client_section = config.get("client")
if not isinstance(client_section, dict):
    raise TypeError("config.toml must contain a [client] table")

config = _parse_client_config(client_section)


def print_config():
    """测试，打印所有配置信息"""

    def clearly_type(obj):
        import re

        result = type(obj).__name__
        match = re.search(r"'(.*?)'", result)
        if match:
            return match.group(1)
        else:
            return result

    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="ClientConfig 配置")
    table.add_column("属性名", style="cyan")
    table.add_column("类型", style="magenta")
    table.add_column("值", style="green")

    for name in ClientConfig.model_fields:
        value = getattr(config, name)
        table.add_row(name, clearly_type(value), str(value))

    console.print(table)


if __name__ == "__main__":
    print_config()
