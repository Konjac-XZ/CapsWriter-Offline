using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.Windows.AppLifecycle;

namespace CapsWriter_WinUI;

public static class Program
{
    private const string MainInstanceKey = "CapsWriter.MainInstance";
    private static AppInstance? _mainInstance;
    private static App? _application;

    [STAThread]
    public static async Task Main(string[] args)
    {
        WinRT.ComWrappersSupport.InitializeComWrappers();

        AppInstance current = AppInstance.GetCurrent();
        AppActivationArguments activationArgs = current.GetActivatedEventArgs();
        _mainInstance = AppInstance.FindOrRegisterForKey(MainInstanceKey);
        if (!_mainInstance.IsCurrent)
        {
            await _mainInstance.RedirectActivationToAsync(activationArgs);
            return;
        }

        _mainInstance.Activated += MainInstance_Activated;
        Application.Start(initializationParams =>
        {
            DispatcherQueueSynchronizationContext context = new(
                DispatcherQueue.GetForCurrentThread());
            SynchronizationContext.SetSynchronizationContext(context);
            _ = initializationParams;
            _application = new App();
        });
    }

    private static void MainInstance_Activated(object? sender, AppActivationArguments args)
    {
        try
        {
            _application?.ShowMainWindow();
        }
        catch
        {
            // Activation must never tear down the resident primary instance.
        }
    }
}
