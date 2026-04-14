import asyncio
from pathlib import Path

from src.infra.gui_output import gui_print
from src.provider.availability_test import run_availability_test, ProviderAvailabilityTester


async def _main():
    # src/provider/ → src/ → project root
    root = Path(__file__).resolve().parent.parent.parent
    # Prefer project-level test audio file; fall back to assets/start.mp3 if needed
    candidates = [
        root / "AvailabilityTest.mp3",
        root / "assets" / "start.mp3",
    ]
    test_audio = None
    for p in candidates:
        if p.exists():
            test_audio = p
            break
    if test_audio is None:
        gui_print("未找到用于测试的音频文件 AvailabilityTest.mp3，已跳过。", color="#ff5555")
        return 2

    def progress(msg: str):
        gui_print(msg, color="#00d4ff")

    try:
        results = await run_availability_test(test_audio, progress_callback=progress)
        tester = ProviderAvailabilityTester(test_audio)
        summary = tester.format_results(results)
        for line in summary.splitlines():
            gui_print(line)
        return 0
    except Exception as e:
        gui_print(f"测试出错: {e}", color="#ff5555")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
