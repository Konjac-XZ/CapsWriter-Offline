import subprocess

def check_process(name):
    # 使用tasklist命令查找进程
    command = ['tasklist', '/FO', 'CSV', '/NH']  # 使用CSV格式输出，不显示标题行

    # 创建STARTUPINFO结构并设置wShowWindow为SW_HIDE
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE

    try:
        # 执行命令并捕获输出
        output = subprocess.check_output(command, startupinfo=si).decode('utf-8', errors='replace')
    except FileNotFoundError:
        print("未找到命令，检查是否安装在环境中。")
        return False

    # 清洗输出并检查进程名称是否在输出中
    for line in output.splitlines():
        # 解析输出，获取进程名称
        parts = line.split('",')
        if len(parts) > 1:
            process_name = parts[0].replace('"', '').lower()
            if process_name == name.lower():
                return True
                
    return False

if __name__ == '__main__':
    print(check_process('notepad.exe'))
