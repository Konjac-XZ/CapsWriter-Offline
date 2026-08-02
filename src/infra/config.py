import sys
from pathlib import Path
from typing import Any, Mapping

import tomllib

# 加载TOML配置文件
# When running from PyInstaller, look for config.toml next to the executable
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    # Running in PyInstaller bundle - config.toml is in the same directory as the .exe
    config_toml_path = Path(sys.executable).parent / "config.toml"
else:
    # Running as script - use relative path (src/infra/ → src/ → project root)
    config_toml_path = Path(__file__).parent.parent.parent / "config.toml"

with config_toml_path.open("r", encoding="utf-8") as f:
    config = tomllib.loads(f.read())


def _section(name: str) -> Mapping[str, Any]:
    section = config.get(name)
    if isinstance(section, dict):
        return section
    return {}


client_cfg = _section("client")


# 客户端配置
class ClientConfig:
    speech_recognition_shortcut: str = str(
        client_cfg.get("speech_recognition_shortcut", "")
    )
    toggle_textbox_context_shortcut: str = str(
        client_cfg.get("toggle_textbox_context_shortcut", "")
    )
    threshold: float = float(client_cfg.get("threshold", 0.0))
    paste: bool = bool(client_cfg.get("paste", False))
    restore_clipboard_after_paste: bool = bool(
        client_cfg.get("restore_clipboard_after_paste", False)
    )
    tsf_speech_tip_enabled: bool = bool(
        client_cfg.get("tsf_speech_tip_enabled", False)
    )
    tsf_speech_tip_ack_timeout_ms: int = max(
        10, int(client_cfg.get("tsf_speech_tip_ack_timeout_ms", 150))
    )
    save_audio: bool = bool(client_cfg.get("save_audio", False))
    save_markdown: bool = bool(client_cfg.get("save_markdown", False))
    audio_name_len: int = int(client_cfg.get("audio_name_len", 0))
    reduce_audio_files: bool = bool(client_cfg.get("reduce_audio_files", False))
    trash_punc: str = str(client_cfg.get("trash_punc", ""))
    mic_seg_duration: int = int(client_cfg.get("mic_seg_duration", 0))
    mic_seg_overlap: int = int(client_cfg.get("mic_seg_overlap", 0))
    file_seg_duration: int = int(client_cfg.get("file_seg_duration", 0))
    file_seg_overlap: int = int(client_cfg.get("file_seg_overlap", 0))
    mute_other_audio: bool = bool(client_cfg.get("mute_other_audio", False))
    pause_other_audio: bool = bool(client_cfg.get("pause_other_audio", False))
    shrink_automatically_to_tray: bool = bool(
        client_cfg.get("shrink_automatically_to_tray", False)
    )
    only_run_once: bool = bool(client_cfg.get("only_run_once", False))
    only_enable_microphones_when_pressed_record_shortcut: bool = bool(
        client_cfg.get("only_enable_microphones_when_pressed_record_shortcut", False)
    )
    vscode_exe_path: str = str(client_cfg.get("vscode_exe_path", ""))
    play_start_music: bool = bool(client_cfg.get("play_start_music", False))
    start_music_path: Path = Path(str(client_cfg.get("start_music_path", "")))
    start_music_volume: str = str(client_cfg.get("start_music_volume", ""))
    play_stop_music: bool = bool(client_cfg.get("play_stop_music", False))
    stop_music_path: Path = Path(str(client_cfg.get("stop_music_path", "")))
    stop_music_volume: str = str(client_cfg.get("stop_music_volume", ""))
    show_listening_overlay: bool = bool(client_cfg.get("show_listening_overlay", True))
    daily_input_log_interval: int = max(
        1, int(client_cfg.get("daily_input_log_interval", 1000))
    )

    check_microphone_usage_by: str = str(
        client_cfg.get("check_microphone_usage_by", "")
    )


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
    config_classes = [ClientConfig]

    for config_class in config_classes:
        table = Table(title=f"{config_class.__name__} 配置")

        table.add_column("属性名", style="cyan")
        table.add_column("类型", style="magenta")
        table.add_column("值", style="green")

        for key, value in config_class.__dict__.items():
            if not key.startswith("_"):
                attr_type = clearly_type(value)
                attr_value = str(value)
                table.add_row(key, attr_type, attr_value)

        console.print(table)


if __name__ == "__main__":
    print_config()
