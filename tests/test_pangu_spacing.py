import unittest

import pangu


class PanguSpacingTest(unittest.TestCase):
    def test_keeps_chinese_smart_double_quotes_attached_to_cjk_text(self) -> None:
        self.assertEqual(
            pangu.spacing_text("中文“类似这样”的测试"),
            "中文“类似这样”的测试",
        )

    def test_keeps_spacing_inside_chinese_smart_double_quotes(self) -> None:
        self.assertEqual(
            pangu.spacing_text("这是“hello世界”的测试"),
            "这是“hello 世界”的测试",
        )
        self.assertEqual(
            pangu.spacing_text("这是中文“AI测试”结果"),
            "这是中文“AI 测试”结果",
        )

    def test_preserves_regular_cjk_ascii_spacing(self) -> None:
        self.assertEqual(
            pangu.spacing_text("中文CapsWriter测试"),
            "中文 CapsWriter 测试",
        )


if __name__ == "__main__":
    unittest.main()
