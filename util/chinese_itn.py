# coding: utf-8
"""中文 ITN：基于 cn2an 的句子级数字转换。"""

__all__ = ["chinese_to_num"]

import cn2an


def chinese_to_num(original: str) -> str:
    """将文本中的中文数字转换为阿拉伯数字。

    使用 cn2an 的句子级转换能力，覆盖日期、分数、百分比等常见场景。
    为兼容口语识别结果，先将“幺”归一为“一”。
    """
    if not original:
        return original

    normalized = original.replace("幺", "一")
    try:
        return cn2an.transform(normalized, "cn2an")
    except Exception:
        return original


if __name__ == "__main__":
    print(chinese_to_num("二零二五年十月"))
    print(chinese_to_num("乱七八糟"))

