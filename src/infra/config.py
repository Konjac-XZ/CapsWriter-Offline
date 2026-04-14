import sys
from pathlib import Path
from typing import Any, Mapping

import tomllib

# 加载TOML配置文件
# When running from PyInstaller, look for config.toml next to the executable
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
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


server_cfg = _section("server")
client_cfg = _section("client")
model_paths_cfg = _section("model_paths")
sensevoice_cfg = _section("sensevoice_args")
paraformer_cfg = _section("paraformer_args")


# 服务端配置
class ServerConfig:
    model: str = str(server_cfg.get("model", ""))
    addr: str = str(server_cfg.get("addr", ""))
    speech_recognition_port: str = str(server_cfg.get("speech_recognition_port", ""))
    format_num: bool = bool(server_cfg.get("format_num", False))
    format_punc: bool = bool(server_cfg.get("format_punc", False))
    format_spell: bool = bool(server_cfg.get("format_spell", False))
    shrink_automatically_to_tray: bool = bool(
        server_cfg.get("shrink_automatically_to_tray", False)
    )
    only_run_once: bool = bool(server_cfg.get("only_run_once", False))
    in_the_meantime_start_the_client: bool = bool(
        server_cfg.get("in_the_meantime_start_the_client", False)
    )
    in_the_meantime_start_the_client_and_run_as_admin: bool = bool(
        server_cfg.get("in_the_meantime_start_the_client_and_run_as_admin", False)
    )


# 客户端配置
class ClientConfig:
    addr: str = str(client_cfg.get("addr", ""))
    speech_recognition_port: str = str(client_cfg.get("speech_recognition_port", ""))
    speech_recognition_shortcut: str = str(client_cfg.get("speech_recognition_shortcut", ""))
    hold_mode: bool = bool(client_cfg.get("hold_mode", False))
    suppress: bool = bool(client_cfg.get("suppress", False))
    restore_key: bool = bool(client_cfg.get("restore_key", False))
    threshold: float = float(client_cfg.get("threshold", 0.0))
    paste: bool = bool(client_cfg.get("paste", False))
    restore_clipboard_after_paste: bool = bool(
        client_cfg.get("restore_clipboard_after_paste", False)
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
    hint_while_recording_at_edit_position_powered_by_ahk: bool = bool(
        client_cfg.get("hint_while_recording_at_edit_position_powered_by_ahk", False)
    )

    check_microphone_usage_by: str = str(client_cfg.get("check_microphone_usage_by", ""))
    enable_double_click_opposite_state: bool = bool(
        client_cfg.get("enable_double_click_opposite_state", False)
    )
    convert_to_traditional_chinese_main: str = str(
        client_cfg.get("convert_to_traditional_chinese_main", "")
    )
    opencc_converter: str = str(client_cfg.get("opencc_converter", ""))
# 模型路径配置
class ModelPaths:
    model_dir: Path = Path(str(model_paths_cfg.get("model_dir", "")))
    sensevoice_path: Path = Path(str(model_paths_cfg.get("sensevoice_path", "")))
    sensevoice_tokens_path: Path = Path(str(model_paths_cfg.get("sensevoice_tokens_path", "")))
    paraformer_path: Path = Path(str(model_paths_cfg.get("paraformer_path", "")))
    paraformer_tokens_path: Path = Path(str(model_paths_cfg.get("paraformer_tokens_path", "")))
    punc_model_dir: Path = Path(str(model_paths_cfg.get("punc_model_dir", "")))


# SenseVoice 参数配置
class SenseVoiceArgs:
    model: str = str(model_paths_cfg.get("sensevoice_path", ""))
    tokens: str = str(model_paths_cfg.get("sensevoice_tokens_path", ""))
    num_threads: int = int(sensevoice_cfg.get("num_threads", 0))
    sample_rate: int = int(sensevoice_cfg.get("sample_rate", 0))
    feature_dim: int = int(sensevoice_cfg.get("feature_dim", 0))
    decoding_method: str = str(sensevoice_cfg.get("decoding_method", ""))
    debug: bool = bool(sensevoice_cfg.get("debug", False))
    provider: str = str(sensevoice_cfg.get("provider", ""))
    language: str = str(sensevoice_cfg.get("language", ""))
    use_itn: bool = bool(sensevoice_cfg.get("use_itn", False))
    rule_fsts: str = str(sensevoice_cfg.get("rule_fsts", ""))
    rule_fars: str = str(sensevoice_cfg.get("rule_fars", ""))


# Paraformer 参数配置
class ParaformerArgs:
    paraformer: str = str(model_paths_cfg.get("paraformer_path", ""))
    tokens: str = str(model_paths_cfg.get("paraformer_tokens_path", ""))
    num_threads: int = int(paraformer_cfg.get("num_threads", 0))
    sample_rate: int = int(paraformer_cfg.get("sample_rate", 0))
    feature_dim: int = int(paraformer_cfg.get("feature_dim", 0))
    decoding_method: str = str(paraformer_cfg.get("decoding_method", ""))
    debug: bool = bool(paraformer_cfg.get("debug", False))


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
    config_classes = [
        ServerConfig,
        ClientConfig,
        ModelPaths,
        SenseVoiceArgs,
        ParaformerArgs,
    ]

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
