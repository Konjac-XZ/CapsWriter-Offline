using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.UI.Xaml;

namespace CapsWriter_WinUI.Services;

internal sealed class NotifyIconService : IDisposable
{
    private const uint CallbackMessage = 0x8000 + 0x51;
    private const uint NimAdd = 0x00000000;
    private const uint NimDelete = 0x00000002;
    private const uint NimSetVersion = 0x00000004;
    private const uint NifMessage = 0x00000001;
    private const uint NifIcon = 0x00000002;
    private const uint NifTip = 0x00000004;
    private const uint NotifyIconVersion4 = 4;
    private const uint WmContextMenu = 0x007B;
    private const uint WmLButtonDoubleClick = 0x0203;
    private const uint WmRButtonUp = 0x0205;
    private const uint NinSelect = 0x0400;
    private const uint NinKeySelect = 0x0401;
    private const uint MfString = 0x0000;
    private const uint MfSeparator = 0x0800;
    private const uint TpmRightButton = 0x0002;
    private const uint TpmReturnCommand = 0x0100;
    private const uint ImageIcon = 1;
    private const uint LrLoadFromFile = 0x0010;

    private const uint ShowCommand = 1;
    private const uint ReloadCommand = 2;
    private const uint RestartCommand = 3;
    private const uint RestartApplicationCommand = 4;
    private const uint QuitCommand = 5;

    private static readonly IntPtr MessageOnlyWindowParent = new(-3);

    private readonly IntPtr _ownerWindowHandle;
    private readonly IntPtr _moduleHandle;
    private readonly string _windowClassName;
    private readonly ushort _windowClassAtom;
    private readonly IntPtr _messageWindowHandle;
    private readonly uint _taskbarCreatedMessage;
    private readonly WindowProcedure _windowProcedure;
    private readonly IntPtr _iconHandle;
    private NotifyIconData _iconData;
    private bool _disposed;

    public event Action? ShowRequested;
    public event Action? ReloadProvidersRequested;
    public event Action? RestartBackendRequested;
    public event Action? RestartApplicationRequested;
    public event Action? QuitRequested;

    public NotifyIconService(Window window)
    {
        _ownerWindowHandle = WinRT.Interop.WindowNative.GetWindowHandle(window);
        _taskbarCreatedMessage = RegisterWindowMessage("TaskbarCreated");
        _windowProcedure = WindowProc;
        _moduleHandle = GetModuleHandle(null);
        _windowClassName = $"CapsWriter.NotifyIcon.{Environment.ProcessId}";
        WindowClass windowClass = new()
        {
            Size = (uint)Marshal.SizeOf<WindowClass>(),
            WindowProcedure = Marshal.GetFunctionPointerForDelegate(_windowProcedure),
            Instance = _moduleHandle,
            ClassName = _windowClassName,
        };
        _windowClassAtom = RegisterClassEx(ref windowClass);
        if (_windowClassAtom == 0)
        {
            throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not register tray window class.");
        }
        _messageWindowHandle = CreateWindowEx(
            0,
            _windowClassName,
            "CapsWriter tray notifications",
            0,
            0,
            0,
            0,
            0,
            MessageOnlyWindowParent,
            IntPtr.Zero,
            _moduleHandle,
            IntPtr.Zero);
        if (_messageWindowHandle == IntPtr.Zero)
        {
            int error = Marshal.GetLastWin32Error();
            UnregisterClass(_windowClassName, _moduleHandle);
            throw new Win32Exception(error, "Could not create tray message window.");
        }

        string? root = RepositoryLocator.FindRoot();
        string iconPath = root is null
            ? Path.Combine(AppContext.BaseDirectory, "Assets", "client-icon.ico")
            : Path.Combine(root, "assets", "client-icon.ico");
        _iconHandle = LoadImage(IntPtr.Zero, iconPath, ImageIcon, 0, 0, LrLoadFromFile);
        _iconData = CreateIconData();
        AddIcon();
    }

    private NotifyIconData CreateIconData() => new()
    {
        Size = (uint)Marshal.SizeOf<NotifyIconData>(),
        WindowHandle = _messageWindowHandle,
        Identifier = 1,
        Flags = NifMessage | NifTip | (_iconHandle == IntPtr.Zero ? 0 : NifIcon),
        CallbackMessage = CallbackMessage,
        IconHandle = _iconHandle,
        Tip = "CapsWriter",
    };

    private void AddIcon()
    {
        if (_disposed)
        {
            return;
        }
        ShellNotifyIcon(NimAdd, ref _iconData);
        _iconData.TimeoutOrVersion = NotifyIconVersion4;
        ShellNotifyIcon(NimSetVersion, ref _iconData);
    }

    private IntPtr WindowProc(IntPtr window, uint message, IntPtr wParam, IntPtr lParam)
    {
        if (message == _taskbarCreatedMessage)
        {
            _iconData = CreateIconData();
            AddIcon();
        }
        else if (message == CallbackMessage)
        {
            uint notification = unchecked((uint)lParam.ToInt64()) & 0xFFFF;
            if (notification is WmLButtonDoubleClick or NinSelect or NinKeySelect)
            {
                ShowRequested?.Invoke();
            }
            else if (notification is WmContextMenu or WmRButtonUp)
            {
                ShowContextMenu();
            }
        }
        return DefWindowProc(window, message, wParam, lParam);
    }

    private void ShowContextMenu()
    {
        IntPtr menu = CreatePopupMenu();
        if (menu == IntPtr.Zero)
        {
            return;
        }
        try
        {
            AppendMenu(menu, MfString, ShowCommand, "显示 CapsWriter");
            AppendMenu(menu, MfSeparator, 0, null);
            AppendMenu(menu, MfString, ReloadCommand, "重新加载转录服务");
            AppendMenu(menu, MfString, RestartCommand, "重启 Python 后端");
            AppendMenu(menu, MfString, RestartApplicationCommand, "重启 CapsWriter");
            AppendMenu(menu, MfSeparator, 0, null);
            AppendMenu(menu, MfString, QuitCommand, "退出 CapsWriter");
            GetCursorPos(out Point cursor);
            SetForegroundWindow(_ownerWindowHandle);
            uint command = TrackPopupMenuEx(
                menu,
                TpmRightButton | TpmReturnCommand,
                cursor.X,
                cursor.Y,
                _messageWindowHandle,
                IntPtr.Zero);
            switch (command)
            {
                case ShowCommand:
                    ShowRequested?.Invoke();
                    break;
                case ReloadCommand:
                    ReloadProvidersRequested?.Invoke();
                    break;
                case RestartCommand:
                    RestartBackendRequested?.Invoke();
                    break;
                case RestartApplicationCommand:
                    RestartApplicationRequested?.Invoke();
                    break;
                case QuitCommand:
                    QuitRequested?.Invoke();
                    break;
            }
            PostMessage(_messageWindowHandle, 0, IntPtr.Zero, IntPtr.Zero);
        }
        finally
        {
            DestroyMenu(menu);
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        ShellNotifyIcon(NimDelete, ref _iconData);
        if (_iconHandle != IntPtr.Zero)
        {
            DestroyIcon(_iconHandle);
        }
        if (_messageWindowHandle != IntPtr.Zero)
        {
            DestroyWindow(_messageWindowHandle);
        }
        if (_windowClassAtom != 0)
        {
            UnregisterClass(_windowClassName, _moduleHandle);
        }
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NotifyIconData
    {
        public uint Size;
        public IntPtr WindowHandle;
        public uint Identifier;
        public uint Flags;
        public uint CallbackMessage;
        public IntPtr IconHandle;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        public string Tip;

        public uint State;
        public uint StateMask;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)]
        public string Info;

        public uint TimeoutOrVersion;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)]
        public string InfoTitle;

        public uint InfoFlags;
        public Guid GuidItem;
        public IntPtr BalloonIconHandle;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Point
    {
        public int X;
        public int Y;
    }

    private delegate IntPtr WindowProcedure(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WindowClass
    {
        public uint Size;
        public uint Style;
        public IntPtr WindowProcedure;
        public int ClassExtraBytes;
        public int WindowExtraBytes;
        public IntPtr Instance;
        public IntPtr Icon;
        public IntPtr Cursor;
        public IntPtr BackgroundBrush;
        public string? MenuName;
        public string ClassName;
        public IntPtr SmallIcon;
    }

    [DllImport(
        "shell32.dll",
        EntryPoint = "Shell_NotifyIconW",
        CharSet = CharSet.Unicode,
        ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ShellNotifyIcon(uint message, ref NotifyIconData data);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern uint RegisterWindowMessage(string message);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern ushort RegisterClassEx(ref WindowClass windowClass);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool UnregisterClass(string className, IntPtr instance);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateWindowEx(
        uint extendedStyle,
        string className,
        string windowName,
        uint style,
        int x,
        int y,
        int width,
        int height,
        IntPtr parent,
        IntPtr menu,
        IntPtr instance,
        IntPtr parameter);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyWindow(IntPtr window);

    [DllImport("user32.dll")]
    private static extern IntPtr DefWindowProc(
        IntPtr window,
        uint message,
        IntPtr wParam,
        IntPtr lParam);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr GetModuleHandle(string? moduleName);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr LoadImage(
        IntPtr instance,
        string name,
        uint type,
        int width,
        int height,
        uint loadFlags);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyIcon(IntPtr icon);

    [DllImport("user32.dll")]
    private static extern IntPtr CreatePopupMenu();

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AppendMenu(IntPtr menu, uint flags, uint identifier, string? text);

    [DllImport("user32.dll")]
    private static extern uint TrackPopupMenuEx(
        IntPtr menu,
        uint flags,
        int x,
        int y,
        IntPtr window,
        IntPtr parameters);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyMenu(IntPtr menu);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetCursorPos(out Point point);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetForegroundWindow(IntPtr window);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PostMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);
}
