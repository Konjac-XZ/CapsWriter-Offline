import uuid
from pathlib import Path
from util.client_cosmic import console, Cosmic


def adjust_srt(file: Path):

    # 生成任务 id
    task_id = str(uuid.uuid1())
    console.print(f'\n任务标识：{task_id}')
    console.print(f'    处理文件：{file}')

    # 已禁用字幕（srt）生成功能
    console.print('    已跳过字幕生成功能（已禁用）')