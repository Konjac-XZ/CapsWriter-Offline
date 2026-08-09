using System.Diagnostics;
using System.Runtime.InteropServices;
using CapsWriter_WinUI.Backdrops;
using CapsWriter_WinUI.Models;
using CapsWriter_WinUI.Services;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Windows.Graphics;

namespace CapsWriter_WinUI;

public sealed partial class StatusOverlayWindow : Window, IDisposable
{
    private const int OverlayWidth = 246;
    private const int OverlayHeight = 38;
    private const int OverlayBottomOffset = 80;
    // Match the legacy Qt meter's 16 ms animation steps: 22% attack and 10% release.
    private const double LevelAttackTimeConstantSeconds = 0.064;
    private const double LevelReleaseTimeConstantSeconds = 0.152;
    private const int MonitorDefaultToNearest = 2;
    private const int GwlExStyle = -20;
    private const int GwlpWndProc = -4;
    private const long WsExToolWindow = 0x00000080L;
    private const long WsExNoActivate = 0x08000000L;
    private const uint WmEraseBackground = 0x0014;
    private const uint WmDwmCompositionChanged = 0x031E;
    private const int DwmWindowBorderColor = 34;
    private const int DwmWindowCornerPreference = 33;
    private const int DwmWindowCornerDoNotRound = 1;
    private const uint DwmBorderColorNone = 0xFFFFFFFE;
    private const uint DwmBlurBehindEnable = 0x00000001;
    private const uint DwmBlurBehindBlurRegion = 0x00000002;
    private readonly PythonServiceClient _client;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMilliseconds(100) };
    private readonly Stopwatch _elapsed = new();
    private double _targetLevel;
    private double _displayLevel;
    private long _lastRenderTimestamp;
    private bool _isLevelRendering;
    private bool _isListeningVisible;
    private bool _disposed;
    private IntPtr _windowHandle;
    private IntPtr _originalWindowProcedure;
    private IntPtr _backgroundBrush;
    private WindowProcedure? _windowProcedure;

    public StatusOverlayWindow(PythonServiceClient client)
    {
        InitializeComponent();
        SystemBackdrop = new TransparentBackdrop();
        _client = client;
        _client.StatusOverlayReceived += Client_StatusOverlayReceived;
        _timer.Tick += Timer_Tick;

        OverlappedPresenter presenter = OverlappedPresenter.CreateForContextMenu();
        presenter.SetBorderAndTitleBar(false, false);
        presenter.IsAlwaysOnTop = true;
        AppWindow.SetPresenter(presenter);
        AppWindow.IsShownInSwitchers = false;
        AppWindow.Resize(new SizeInt32(OverlayWidth, OverlayHeight));

        _windowHandle = WinRT.Interop.WindowNative.GetWindowHandle(this);
        long extendedStyle = GetWindowLongPointer(_windowHandle, GwlExStyle).ToInt64();
        SetWindowLongPointer(
            _windowHandle,
            GwlExStyle,
            new IntPtr(extendedStyle | WsExToolWindow | WsExNoActivate));
        _windowProcedure = OverlayWindowProcedure;
        _originalWindowProcedure = SetWindowLongPointer(
            _windowHandle,
            GwlpWndProc,
            Marshal.GetFunctionPointerForDelegate(_windowProcedure));
        int cornerPreference = DwmWindowCornerDoNotRound;
        DwmSetWindowAttribute(
            _windowHandle,
            DwmWindowCornerPreference,
            ref cornerPreference,
            Marshal.SizeOf<int>());
        uint borderColor = DwmBorderColorNone;
        DwmSetWindowAttribute(
            _windowHandle,
            DwmWindowBorderColor,
            ref borderColor,
            Marshal.SizeOf<uint>());
        EnableTransparentComposition(_windowHandle);
        ClearInitialBackground();
    }

    private IntPtr OverlayWindowProcedure(
        IntPtr windowHandle,
        uint message,
        IntPtr wParam,
        IntPtr lParam)
    {
        if (message == WmEraseBackground && ClearBackground(windowHandle, wParam))
        {
            return new IntPtr(1);
        }
        if (message == WmDwmCompositionChanged)
        {
            EnableTransparentComposition(windowHandle);
            return IntPtr.Zero;
        }
        return CallWindowProc(
            _originalWindowProcedure,
            windowHandle,
            message,
            wParam,
            lParam);
    }

    private void ClearInitialBackground()
    {
        IntPtr deviceContext = GetDC(_windowHandle);
        if (deviceContext == IntPtr.Zero)
        {
            return;
        }
        try
        {
            ClearBackground(_windowHandle, deviceContext);
        }
        finally
        {
            ReleaseDC(_windowHandle, deviceContext);
        }
    }

    private bool ClearBackground(IntPtr windowHandle, IntPtr deviceContext)
    {
        if (deviceContext == IntPtr.Zero || !GetClientRect(windowHandle, out Rect rect))
        {
            return false;
        }
        if (_backgroundBrush == IntPtr.Zero)
        {
            // With the DWM frame extended into the client area, black is the
            // transparent key color rather than an opaque painted background.
            _backgroundBrush = CreateSolidBrush(0);
        }
        return _backgroundBrush != IntPtr.Zero
            && FillRect(deviceContext, ref rect, _backgroundBrush) != 0;
    }

    private static void EnableTransparentComposition(IntPtr windowHandle)
    {
        Margins margins = default;
        DwmExtendFrameIntoClientArea(windowHandle, ref margins);

        IntPtr blurRegion = CreateRectRgn(-2, -2, -1, -1);
        if (blurRegion == IntPtr.Zero)
        {
            return;
        }
        try
        {
            DwmBlurBehind blurBehind = new()
            {
                Flags = DwmBlurBehindEnable | DwmBlurBehindBlurRegion,
                Enable = true,
                BlurRegion = blurRegion,
            };
            DwmEnableBlurBehindWindow(windowHandle, ref blurBehind);
        }
        finally
        {
            DeleteObject(blurRegion);
        }
    }

    private void Client_StatusOverlayReceived(object? sender, StatusOverlayEvent overlayEvent) =>
        DispatcherQueue.TryEnqueue(() => ApplyEvent(overlayEvent));

    public void ApplyExternalEvent(StatusOverlayEvent overlayEvent) => ApplyEvent(overlayEvent);

    private void ApplyEvent(StatusOverlayEvent overlayEvent)
    {
        if (overlayEvent.Action == "level")
        {
            // The Python overlay consumes this same normalized value directly.
            // Keep interpolation in the render loop so short peaks are not
            // attenuated by a second low-pass filter or a UI-only noise gate.
            _targetLevel = Math.Clamp(overlayEvent.Level ?? 0, 0, 1);
            if (_isListeningVisible)
            {
                StartLevelRendering();
            }
            return;
        }
        if (overlayEvent.Action == "hide")
        {
            HideOverlay();
            return;
        }
        if (overlayEvent.Action != "show")
        {
            return;
        }

        string state = overlayEvent.State ?? "listening";
        StateText.Text = state switch
        {
            "transcribing" => "转录中",
            "polishing" => "润色中",
            _ => "正在监听",
        };
        SolidColorBrush stateBackground = new(state switch
        {
            "transcribing" => Windows.UI.Color.FromArgb(235, 166, 111, 0),
            "polishing" => Windows.UI.Color.FromArgb(235, 24, 121, 78),
            _ => Windows.UI.Color.FromArgb(235, 20, 24, 28),
        });
        StatusPane.Background = stateBackground;
        AbandonButton.Background = stateBackground;
        _isListeningVisible = state == "listening";
        LevelFill.Visibility = _isListeningVisible ? Visibility.Visible : Visibility.Collapsed;
        ResetLevelMeter();
        if (_isListeningVisible)
        {
            StartLevelRendering();
        }
        _elapsed.Restart();
        _timer.Start();
        UpdateElapsed();
        MoveToCursorDisplay();
        AppWindow.Show(false);
    }

    private void HideOverlay()
    {
        _timer.Stop();
        _elapsed.Reset();
        _isListeningVisible = false;
        ResetLevelMeter();
        AppWindow.Hide();
    }

    private void StartLevelRendering()
    {
        if (_isLevelRendering)
        {
            return;
        }
        _isLevelRendering = true;
        _lastRenderTimestamp = Stopwatch.GetTimestamp();
        CompositionTarget.Rendering += CompositionTarget_Rendering;
    }

    private void StopLevelRendering()
    {
        if (!_isLevelRendering)
        {
            return;
        }
        CompositionTarget.Rendering -= CompositionTarget_Rendering;
        _isLevelRendering = false;
        _lastRenderTimestamp = 0;
    }

    private void ResetLevelMeter()
    {
        StopLevelRendering();
        _targetLevel = 0;
        _displayLevel = 0;
        LevelScaleTransform.ScaleX = 0;
    }

    private void CompositionTarget_Rendering(object? sender, object args)
    {
        if (!_isListeningVisible)
        {
            StopLevelRendering();
            return;
        }

        long now = Stopwatch.GetTimestamp();
        double elapsedSeconds = Stopwatch.GetElapsedTime(_lastRenderTimestamp, now).TotalSeconds;
        _lastRenderTimestamp = now;
        // A delayed UI frame should catch up over several render frames instead
        // of turning a scheduling hiccup into a visible meter jump.
        elapsedSeconds = Math.Clamp(elapsedSeconds, 0, 1.0 / 30.0);

        double timeConstant = _targetLevel > _displayLevel
            ? LevelAttackTimeConstantSeconds
            : LevelReleaseTimeConstantSeconds;
        double blend = 1 - Math.Exp(-elapsedSeconds / timeConstant);
        _displayLevel += (_targetLevel - _displayLevel) * blend;
        if (Math.Abs(_displayLevel - _targetLevel) < 0.0005)
        {
            _displayLevel = _targetLevel;
        }
        LevelScaleTransform.ScaleX = _displayLevel;
    }

    private void Timer_Tick(object? sender, object e) => UpdateElapsed();

    private void UpdateElapsed() =>
        ElapsedText.Text = $"{Math.Min(_elapsed.Elapsed.TotalSeconds, 999.9),5:0.0}s";

    private void MoveToCursorDisplay()
    {
        GetCursorPos(out Point cursor);
        IntPtr monitor = MonitorFromPoint(cursor, MonitorDefaultToNearest);
        MonitorInfo info = new() { Size = Marshal.SizeOf<MonitorInfo>() };
        if (!GetMonitorInfo(monitor, ref info))
        {
            return;
        }
        int x = info.WorkArea.Left
            + (info.WorkArea.Right - info.WorkArea.Left - OverlayWidth) / 2;
        int y = info.WorkArea.Bottom - OverlayHeight - OverlayBottomOffset;
        AppWindow.Move(new PointInt32(x, y));
    }

    private async void Abandon_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            await _client.AbandonCurrentAsync();
            HideOverlay();
        }
        catch (Exception exception)
        {
            _client.ReportLocalLog($"放弃当前任务失败：{exception.Message}", "#C42B1C");
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        _client.StatusOverlayReceived -= Client_StatusOverlayReceived;
        _timer.Stop();
        StopLevelRendering();
        if (_windowHandle != IntPtr.Zero && _originalWindowProcedure != IntPtr.Zero)
        {
            SetWindowLongPointer(_windowHandle, GwlpWndProc, _originalWindowProcedure);
            _originalWindowProcedure = IntPtr.Zero;
        }
        _windowProcedure = null;
        if (_backgroundBrush != IntPtr.Zero)
        {
            DeleteObject(_backgroundBrush);
            _backgroundBrush = IntPtr.Zero;
        }
        Close();
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Point
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Rect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MonitorInfo
    {
        public int Size;
        public Rect Monitor;
        public Rect WorkArea;
        public uint Flags;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct Margins
    {
        public int Left;
        public int Right;
        public int Top;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct DwmBlurBehind
    {
        public uint Flags;

        [MarshalAs(UnmanagedType.Bool)]
        public bool Enable;

        public IntPtr BlurRegion;

        [MarshalAs(UnmanagedType.Bool)]
        public bool TransitionOnMaximized;
    }

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    private delegate IntPtr WindowProcedure(
        IntPtr windowHandle,
        uint message,
        IntPtr wParam,
        IntPtr lParam);

    private static IntPtr GetWindowLongPointer(IntPtr window, int index) =>
        IntPtr.Size == 8
            ? GetWindowLongPtr64(window, index)
            : new IntPtr(GetWindowLong32(window, index));

    private static IntPtr SetWindowLongPointer(IntPtr window, int index, IntPtr value) =>
        IntPtr.Size == 8
            ? SetWindowLongPtr64(window, index, value)
            : new IntPtr(SetWindowLong32(window, index, value.ToInt32()));

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetCursorPos(out Point point);

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromPoint(Point point, int flags);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr GetWindowLongPtr64(IntPtr window, int index);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)]
    private static extern int GetWindowLong32(IntPtr window, int index);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtr64(IntPtr window, int index, IntPtr value);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern int SetWindowLong32(IntPtr window, int index, int value);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr CallWindowProc(
        IntPtr previousWindowProcedure,
        IntPtr window,
        uint message,
        IntPtr wParam,
        IntPtr lParam);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetClientRect(IntPtr window, out Rect rect);

    [DllImport("user32.dll")]
    private static extern IntPtr GetDC(IntPtr window);

    [DllImport("user32.dll")]
    private static extern int ReleaseDC(IntPtr window, IntPtr deviceContext);

    [DllImport("user32.dll")]
    private static extern int FillRect(
        IntPtr deviceContext,
        ref Rect rect,
        IntPtr brush);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(
        IntPtr window,
        int attribute,
        ref int value,
        int valueSize);

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(
        IntPtr window,
        int attribute,
        ref uint value,
        int valueSize);

    [DllImport("dwmapi.dll")]
    private static extern int DwmExtendFrameIntoClientArea(
        IntPtr window,
        ref Margins margins);

    [DllImport("dwmapi.dll")]
    private static extern int DwmEnableBlurBehindWindow(
        IntPtr window,
        ref DwmBlurBehind blurBehind);

    [DllImport("gdi32.dll")]
    private static extern IntPtr CreateRectRgn(int left, int top, int right, int bottom);

    [DllImport("gdi32.dll")]
    private static extern IntPtr CreateSolidBrush(uint color);

    [DllImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeleteObject(IntPtr graphicsObject);
}
