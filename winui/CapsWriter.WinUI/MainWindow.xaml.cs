using Microsoft.UI.Windowing;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.Windows.AppLifecycle;
using System.Runtime.InteropServices;
using CapsWriter_WinUI.Pages;
using CapsWriter_WinUI.Services;

// To learn more about WinUI, the WinUI project structure,
// and more about our project templates, see: http://aka.ms/winui-project-info.

namespace CapsWriter_WinUI;

public sealed partial class MainWindow : Window
{
    private NotifyIconService? _notifyIcon;
    private bool _allowClose;

    public MainWindow()
    {
        InitializeComponent();

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
        AppWindow.TitleBar.ButtonBackgroundColor = Colors.Transparent;
        AppWindow.TitleBar.ButtonInactiveBackgroundColor = Colors.Transparent;
        string iconPath = Path.Combine(AppContext.BaseDirectory, "Assets", "client-icon.ico");
        if (File.Exists(iconPath))
        {
            AppWindow.SetIcon(iconPath);
        }
        PlaceAsDesktopMonitor();
        AppWindow.Closing += MainWindow_Closing;
        Closed += MainWindow_Closed;
    }

    public void InitializeDesktopServices()
    {
        if (_notifyIcon is not null)
        {
            return;
        }
        _notifyIcon = new NotifyIconService(this);
        _notifyIcon.ShowRequested += ShowFromTray;
        _notifyIcon.ReloadProvidersRequested += () => _ = ReloadProvidersFromTrayAsync();
        _notifyIcon.RestartBackendRequested += () => _ = RestartBackendFromTrayAsync();
        _notifyIcon.RestartApplicationRequested += RestartApplicationFromTray;
        _notifyIcon.QuitRequested += QuitFromTray;
    }

    private void PlaceAsDesktopMonitor()
    {
        DisplayArea area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary);
        Windows.Graphics.RectInt32 workArea = area.WorkArea;
        int width = Math.Min(720, workArea.Width - 24);
        int height = Math.Min(640, workArea.Height - 24);
        AppWindow.MoveAndResize(new Windows.Graphics.RectInt32(
            workArea.X + workArea.Width - width - 12,
            workArea.Y + workArea.Height - height - 12,
            width,
            height));
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.PreferredMinimumWidth = 560;
            presenter.PreferredMinimumHeight = 480;
        }
    }

    private void MainWindow_Closing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (_allowClose)
        {
            return;
        }
        args.Cancel = true;
        sender.Hide();
    }

    private void ShowFromTray() => ShowWindow();

    internal void ShowWindow()
    {
        if (AppWindow.Presenter is OverlappedPresenter presenter
            && presenter.State == OverlappedPresenterState.Minimized)
        {
            presenter.Restore();
        }
        AppWindow.Show();
        Activate();
        SetForegroundWindow(WinRT.Interop.WindowNative.GetWindowHandle(this));
    }

    private void HideWindow_Invoked(
        KeyboardAccelerator sender,
        KeyboardAcceleratorInvokedEventArgs args)
    {
        AppWindow.Hide();
        args.Handled = true;
    }

    private async Task ReloadProvidersFromTrayAsync()
    {
        try
        {
            await ((App)Application.Current).ServiceClient.ReloadProvidersAsync();
            ((App)Application.Current).ServiceClient.ReportLocalLog("已重新加载转录服务配置。", "#107C10");
        }
        catch (Exception exception)
        {
            ((App)Application.Current).ServiceClient.ReportLocalLog($"重新加载转录服务失败：{exception.Message}", "#C42B1C");
        }
    }

    private async Task RestartBackendFromTrayAsync()
    {
        try
        {
            await ((App)Application.Current).ServiceClient.RestartAsync();
        }
        catch (Exception exception)
        {
            ((App)Application.Current).ServiceClient.ReportLocalLog($"重启 Python 后端失败：{exception.Message}", "#C42B1C");
        }
    }

    private void RestartApplicationFromTray()
    {
        var reason = AppInstance.Restart(string.Empty);
        ((App)Application.Current).ServiceClient.ReportLocalLog(
            $"重启 CapsWriter 失败：{reason}",
            "#C42B1C");
    }

    private void QuitFromTray()
    {
        _allowClose = true;
        Close();
    }

    private void NavView_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.SelectedItem is NavigationViewItem item)
        {
            Type pageType = item.Tag switch
            {
                "home" => typeof(HomePage),
                "configuration" => typeof(ConfigurationPage),
                "reflection" => typeof(ReflectionPage),
                "tsf" => typeof(TsfPage),
                "diagnostics" => typeof(DiagnosticsPage),
                _ => throw new InvalidOperationException($"Unknown navigation item tag: {item.Tag}"),
            };
            if (NavFrame.CurrentSourcePageType != pageType)
            {
                NavFrame.Navigate(pageType);
                NavFrame.BackStack.Clear();
            }
        }
    }

    private void NavView_Loaded(object sender, RoutedEventArgs args)
    {
        // Left mode keeps expansion inline, but its default visual state is open.
        // Close it after template initialization so launches start compact.
        NavView.IsPaneOpen = false;
    }

    private async void MainWindow_Closed(object sender, WindowEventArgs args)
    {
        _notifyIcon?.Dispose();
        if (Application.Current is App app)
        {
            await app.ShutdownAsync();
        }
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetForegroundWindow(IntPtr window);
}
