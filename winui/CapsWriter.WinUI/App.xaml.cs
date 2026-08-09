using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Navigation;
using Microsoft.UI.Dispatching;
using CapsWriter_WinUI.Services;

// To learn more about WinUI, the WinUI project structure,
// and more about our project templates, see: http://aka.ms/winui-project-info.

namespace CapsWriter_WinUI;

/// <summary>
/// Provides application-specific behavior to supplement the default Application class.
/// </summary>
public partial class App : Application
{
    private Window? _window;
    private StatusOverlayWindow? _statusOverlay;
    private DispatcherQueue? _mainDispatcherQueue;
    private bool _showRequestedBeforeLaunch;
    public PythonServiceClient ServiceClient { get; } = new();
    public LatestAudioPlayer AudioPlayer { get; } = new();

    /// <summary>
    /// Initializes the singleton application object.  This is the first line of authored code
    /// executed, and as such is the logical equivalent of main() or WinMain().
    /// </summary>
    public App()
    {
        InitializeComponent();
    }

    /// <summary>
    /// Invoked when the application is launched.
    /// </summary>
    /// <param name="args">Details about the launch request and process.</param>
    protected override void OnLaunched(Microsoft.UI.Xaml.LaunchActivatedEventArgs args)
    {
        MainWindow mainWindow = new();
        _window = mainWindow;
        _mainDispatcherQueue = mainWindow.DispatcherQueue;
        mainWindow.Activate();
        mainWindow.InitializeDesktopServices();
        ServiceClient.StatusOverlayReceived += ServiceClient_StatusOverlayReceived;
        bool skipBackend = args.Arguments
            .Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Any(argument => string.Equals(argument, "--no-backend", StringComparison.OrdinalIgnoreCase));
        if (!skipBackend && !string.Equals(
                Environment.GetEnvironmentVariable("CAPSWRITER_WINUI_NO_BACKEND"),
                "1",
                StringComparison.Ordinal))
        {
            _ = ServiceClient.StartAsync();
        }
        if (_showRequestedBeforeLaunch)
        {
            _showRequestedBeforeLaunch = false;
            mainWindow.ShowWindow();
        }
    }

    public void ShowMainWindow()
    {
        DispatcherQueue? dispatcherQueue = _mainDispatcherQueue;
        if (dispatcherQueue is null)
        {
            _showRequestedBeforeLaunch = true;
            return;
        }
        dispatcherQueue.TryEnqueue(() =>
        {
            if (_window is MainWindow mainWindow)
            {
                mainWindow.ShowWindow();
            }
        });
    }

    private void ServiceClient_StatusOverlayReceived(
        object? sender,
        Models.StatusOverlayEvent overlayEvent)
    {
        _window?.DispatcherQueue.TryEnqueue(() =>
        {
            if (_statusOverlay is null)
            {
                ServiceClient.StatusOverlayReceived -= ServiceClient_StatusOverlayReceived;
                _statusOverlay = new StatusOverlayWindow(ServiceClient);
            }
            _statusOverlay.ApplyExternalEvent(overlayEvent);
        });
    }

    public async Task ShutdownAsync()
    {
        ServiceClient.StatusOverlayReceived -= ServiceClient_StatusOverlayReceived;
        _statusOverlay?.Dispose();
        _statusOverlay = null;
        AudioPlayer.Dispose();
        await ServiceClient.DisposeAsync();
    }
}
