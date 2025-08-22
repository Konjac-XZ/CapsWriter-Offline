
# 离线翻译将光标选中的中文翻译并替换为英文

# .\runtime\python.exe .\util\client_translate_and_replace_selected_text.py




import asyncio
import keyboard
import clipman
from config import ClientConfig as Config
from util.client_cosmic import Cosmic, console

async def on_hotkey_pressed():
    try:
        # 复制选中的文本
        if keyboard.is_pressed('ctrl'):
            keyboard.release('ctrl')
        if keyboard.is_pressed('alt'):
            keyboard.release('alt')
        keyboard.send('ctrl + c')
        await asyncio.sleep(0.1)  # 等待剪贴板更新
        # 保存剪切板
        try:
            # 初始化剪贴板模块
            clipman.init()
            temp = clipman.get()
        except clipman.exceptions.ClipmanBaseException as e:
            temp = e
            console.print(e)
        text = temp
        # 控制台输出
        console.print(f'选中文本：{text}')
        # 翻译文本
        trans_text = translation(text)[0]['translation_text']
        # 控制台输出翻译结果
        console.print(f'翻译结果：{trans_text}')
        # 打字
        clipman.set(trans_text)
        keyboard.send('ctrl + v')
    except Exception as e:
        console.print(e)

def hotkey_callback():
    asyncio.run_coroutine_threadsafe(on_hotkey_pressed(), loop)

async def translate_and_replace_selected_text():
    try:
        # 获取事件循环
        global loop
        loop = asyncio.get_event_loop()

        # 注册热键
        keyboard.add_hotkey(Config.trans_and_replace_selected_shortcut, hotkey_callback)
        
        await asyncio.Event().wait()  # 阻塞当前协程，直到被取消或热键被触发
    except Exception as e:
        console.print(e)
    finally:
        keyboard.remove_hotkey(Config.trans_and_replace_selected_shortcut)  # 移除热键

if __name__ == '__main__':
    from transformers import pipeline, AutoModelWithLMHead, AutoTokenizer
    #离线翻译
    modelName = ".\models\Helsinki-NLP--opus-mt-zh-en"
    # 加载模型
    model = AutoModelWithLMHead.from_pretrained(modelName, local_files_only=True)
    # 加载分词器
    tokenizer = AutoTokenizer.from_pretrained(modelName, local_files_only=True)
    # 创建离线翻译管道
    translation = pipeline('translation_zh_to_en', model=model, tokenizer=tokenizer)
    asyncio.run(translate_and_replace_selected_text())
