"""读取 config/regex.yaml 中的正则替换规则，对识别文本进行批量替换。"""

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 定位 config/regex.yaml（兼容 PyInstaller 打包与脚本运行）
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _config_dir = Path(sys.executable).parent / "config"
else:
    # src/pipeline/ → src/ → project root → config/
    _config_dir = Path(__file__).parent.parent.parent / "config"

_regex_yaml_path = _config_dir / "regex.yaml"

# ---------------------------------------------------------------------------
# 加载并编译规则
# ---------------------------------------------------------------------------
_compiled_rules: list[tuple[re.Pattern, str]] = []


def _load_rules() -> list[tuple[re.Pattern, str]]:
    """解析 regex.yaml，返回 (compiled_pattern, replacement) 列表。"""
    if not _regex_yaml_path.exists():
        return []

    try:
        import yaml  # PyYAML
    except ImportError:
        print("[regex_replace] 未安装 PyYAML，跳过正则替换规则加载")
        return []

    try:
        with _regex_yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"[regex_replace] 读取 {_regex_yaml_path} 失败: {e}")
        return []

    if not data or not isinstance(data.get("rules"), list):
        return []

    rules: list[tuple[re.Pattern, str]] = []
    for idx, item in enumerate(data["rules"]):
        if not isinstance(item, dict):
            continue
        if not item.get("enabled", True):
            continue
        pattern_str = item.get("pattern")
        replacement = item.get("replacement", "")
        ignore_case = item.get("ignore_case", True)  # 默认设置为不区分大小写
        if not pattern_str:
            continue
        try:
            flags = re.IGNORECASE if ignore_case else 0
            compiled = re.compile(pattern_str, flags=flags)
            rules.append((compiled, replacement))
        except re.error as e:
            print(
                f"[regex_replace] 规则 #{idx} 正则编译失败: {e}  (pattern={pattern_str!r})"
            )

    return rules


# 模块加载时即编译规则
_compiled_rules = _load_rules()


def regex_replace(text: str) -> str:
    """按顺序对 *text* 执行所有已启用的正则替换规则。"""
    for pattern, replacement in _compiled_rules:
        text = pattern.sub(replacement, text)
    return text
