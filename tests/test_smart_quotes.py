import unittest

from src.polish.smart_quotes import normalize_zh_cn_smart_quotes


class SmartQuotesTest(unittest.TestCase):
    def test_normalizes_chinese_double_and_single_quotes(self) -> None:
        text = "她说\"他回答'好的'\"。"
        self.assertEqual(normalize_zh_cn_smart_quotes(text), "她说“他回答‘好的’”。")

    def test_pairs_quotes_across_markdown_emphasis_markers(self) -> None:
        text = '他说"这段**加粗**文本"值得看。'
        self.assertEqual(
            normalize_zh_cn_smart_quotes(text), "他说“这段**加粗**文本”值得看。"
        )

    def test_prefers_measure_marks_and_english_apostrophes(self) -> None:
        text = "他说这个人 6'2\"，但 don't 和 students' 不应该变成中文单引号。"
        self.assertEqual(
            normalize_zh_cn_smart_quotes(text),
            "他说这个人 6′2″，但 don’t 和 students’ 不应该变成中文单引号。",
        )

    def test_protects_markdown_code_url_path_and_link_destination(self) -> None:
        text = (
            '正文里的"引号"和 `const name = "value"`，'
            '链接["标题"](https://example.com/?q="raw")，'
            '路径 C:\\Temp\\"raw"。'
        )
        self.assertEqual(
            normalize_zh_cn_smart_quotes(text),
            '正文里的“引号”和 `const name = "value"`，'
            '链接[“标题”](https://example.com/?q="raw")，'
            '路径 C:\\Temp\\"raw"。',
        )

    def test_protects_frontmatter_and_fenced_code(self) -> None:
        text = (
            "---\n"
            'title: "raw"\n'
            "---\n"
            "\n"
            '正文"引号"。\n'
            "\n"
            "```ts\n"
            'const value = "test"\n'
            "```\n"
        )
        self.assertEqual(
            normalize_zh_cn_smart_quotes(text),
            "---\n"
            'title: "raw"\n'
            "---\n"
            "\n"
            "正文“引号”。\n"
            "\n"
            "```ts\n"
            'const value = "test"\n'
            "```\n",
        )

    def test_skips_structured_json_like_text(self) -> None:
        text = '{"message": "你好"}'
        self.assertEqual(normalize_zh_cn_smart_quotes(text), text)


if __name__ == "__main__":
    unittest.main()
