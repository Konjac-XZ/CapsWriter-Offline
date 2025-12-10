'''

pip install pywin32

pywin32 集成进 Windows embeddable package Python 难

已弃用，仅存档

'''




import os
from win32com.shell import shell, shellcon


def create_shortcut(path, target, arguments="", work_dir=os.getcwd(), icon="", description=""):
    import pythoncom
    from win32com.shell import shell, shellcon
    pythoncom.CoInitialize()
    shortcut = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink, None,
        pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink
    )
    shortcut.SetPath(target)
    if arguments:
        shortcut.SetArguments(arguments)
    if work_dir:
        shortcut.SetWorkingDirectory(work_dir)
    if icon:
        shortcut.SetIconLocation(icon, 0)
    if description:
        shortcut.SetDescription(description)

    persist_file = shortcut.QueryInterface(pythoncom.IID_IPersistFile)
    persist_file.Save(path, 0)


def AddStartupWithWindows(relative_exe_path):
    absolute_path = os.path.abspath(relative_exe_path)
    # 获取启动文件夹的路径
    startup_folder = shell.SHGetFolderPath(0, shellcon.CSIDL_STARTUP, None, 0)
    # 创建到启动文件夹的快捷方式
    startup_path = os.path.join(startup_folder, f'{relative_exe_path}.lnk')
    create_shortcut(startup_path, absolute_path)


if __name__ == "__main__":
    # 添加服务端开机启动项
    AddStartupWithWindows('start_server_gui.exe')