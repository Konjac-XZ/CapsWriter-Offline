import ipaddress
from pathlib import Path

from keyboard import parse_hotkey


class ValueCheck:
    """
    is_ip(ip_text: str) -> (bool, str)

    is_local_listenable_ip(ip_text: str) -> (bool, str)

    is_local_listenable_port(port_text: str) -> (bool, str)

    is_file_exist(file_path: str, suffix: str = None) -> (bool, str)

    is_dir_exist(dir_path: str) -> (bool, str)

    is_hotkey(hotkey: str) -> (bool, str)
    """

    @staticmethod
    def is_ip(ip_text: str):
        try:
            ipaddress.IPv4Address(ip_text)
            return True, None
        except ValueError:
            return False, "不是有效的IP地址"

    @staticmethod
    def is_local_listenable_ip(ip_text: str):
        is_valid, error = ValueCheck.is_ip(ip_text)
        if not is_valid:
            return False, error
        ip = ipaddress.IPv4Address(ip_text)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip == ipaddress.IPv4Address("0.0.0.0")
        ):
            return True, None
        else:
            return False, "不是有效的本地可监听IP地址"

    @staticmethod
    def is_local_listenable_port(port_text: str):
        try:
            port = int(port_text)
            if 1023 <= port <= 65535:
                return True, None
            else:
                return False, "端口号必须在1023到65535之间"
        except ValueError:
            return False, "不是有效的端口号"

    @staticmethod
    def is_file_exist(file_path: str, suffix: str = None):
        file = Path(file_path)
        if not file.exists():
            return False, "文件不存在"
        if not file.is_file():
            return False, "不是有效的文件"
        if suffix is not None and file.suffix != suffix:
            return False, f"文件格式不正确，预期后缀为{suffix}"
        return True, None

    @staticmethod
    def is_dir_exist(dir_path: str):
        dir = Path(dir_path)
        if dir.exists() and dir.is_dir():
            return True, None
        else:
            return False, "目录不存在"

    @staticmethod
    def is_hotkey(hotkey: str):
        try:
            parse_hotkey(hotkey)
            return True, None
        except ValueError:
            return False, "快捷键格式不正确"


if __name__ == "__main__":
    # 测试
    from rich import print

    # 测试一些IP地址
    server_addr_list = [
        "192.168.1.1",
        "10.5.5.5",
        "172.20.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "8.8.8.8",
        "255.255.255.255",
        "invalid_ip",
    ]

    for ip in server_addr_list:
        is_valid, error = ValueCheck.is_local_listenable_ip(ip)
        if is_valid:
            print(f"[green]{ip}[/green]")
        else:
            print(f"[red]{ip} - {error if error else '无效'}[/red]")

    # 测试一些IP地址
    client_addr_list = [
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "invalid_ip",
    ]

    for ip in client_addr_list:
        is_valid, error = ValueCheck.is_ip(ip)
        if is_valid:
            print(f"[green]{ip}[/green]")
        else:
            print(f"[red]{ip} - {error if error else '无效'}[/red]")

    # 测试一些端口号
    server_port_list = [
        "80",
        "443",
        "10000",
        "65535",
        "invalid_port",
    ]

    for port in server_port_list:
        is_valid, error = ValueCheck.is_local_listenable_port(port)
        if is_valid:
            print(f"[green]{port}[/green]")
        else:
            print(f"[red]{port} - {error if error else '无效'}[/red]")

    # 测试一些文件路径
    file_path_list = [
        [r"C:\SSS\VSCode\Code - Insiders.exe", ".exe"],
        ["assets/start.mp3", ".mp3"],
        ["assets/start.mp3", ".wav"],
        ["assets/stop.mp3", ".mp3"],
        ["deeplx_windows_amd64.exe", ".exe"],
        ["models", None],
        [
            "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.onnx",
            ".onnx",
        ],
        [
            "models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/tokens.txt",
            ".txt",
        ],
        [
            "models/paraformer-offline-zh/model.int8.onnx",
            ".onnx",
        ],
        [
            "models/paraformer-offline-zh/tokens.txt",
            ".txt",
        ],
        [
            "models/punc_ct-transformer_cn-en",
            None,
        ],
        [
            "models/Helsinki-NLP--opus-mt-zh-en",
            None,
        ],
        [r"C:\ABC", None],
    ]

    for file_path in file_path_list:
        is_valid, error = ValueCheck.is_file_exist(file_path[0], file_path[1])
        if is_valid:
            print(f"[green]{file_path[0]}[/green]")
        else:
            print(f"[red]{file_path[0]} - {error if error else '无效'}[/red]")

    # 测试快捷键
    hotkey_list = [
        "caps lock",
        "left shift",
        "ctrl + alt + p",
        "right shift",
        "ctrl + alt + [",
        "ctrl + alt + f",
        "shift",
        "caps lock + left shift",
        "caps lock + right shift",
        "f13",
        "f13+left shift",
        "f999",
    ]

    for hotkey in hotkey_list:
        is_valid, error = ValueCheck.is_hotkey(hotkey)
        if is_valid:
            print(f"[green]{hotkey}[/green]")
        else:
            print(f"[red]{hotkey} - {error if error else '无效'}[/red]")
