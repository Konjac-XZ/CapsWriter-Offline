import asyncio
import platform

import clipman
import keyboard

from util.config import ClientConfig as Config


async def type_result(text):
    # 模拟粘贴
    if Config.paste:
        # 保存剪切板
        try:
            # 初始化剪贴板模块
            clipman.init()
            temp = clipman.get()
        except clipman.exceptions.ClipmanBaseException as e:
            temp = e
            print(e)

        # 复制结果
        clipman.set(text)

        # 粘贴结果
        if platform.system() == "Darwin":  # Mac
            keyboard.press(55)
            keyboard.press(9)
            keyboard.release(55)
            keyboard.release(9)
        else:
            keyboard.send("ctrl + v")

        # 还原剪贴板
        if Config.restore_clipboard_after_paste:
            await asyncio.sleep(0.1)
            clipman.set(temp)

    # 模拟打印
    else:
        keyboard.write(text)


# 直打（用于流式增量输出）。不改动 Config.paste，以调用方控制“键入”与“粘贴”。
async def type_keys(text: str):
    keyboard.write(text)


# 明确走粘贴（用于最终合并输出）。
async def type_paste(text: str):
    await type_result(text)
