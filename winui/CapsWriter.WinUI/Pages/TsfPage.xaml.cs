using CapsWriter_WinUI.Models;
using CapsWriter_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CapsWriter_WinUI.Pages;

public sealed partial class TsfPage : Page
{
    private readonly PythonServiceClient _client;

    public TsfPage()
    {
        InitializeComponent();
        _client = ((App)Application.Current).ServiceClient;
    }

    private void Page_Loaded(object sender, RoutedEventArgs e)
    {
        _client.SnapshotReceived += Client_SnapshotReceived;
        if (_client.LastSnapshot is { } snapshot) Apply(snapshot.Tsf);
    }

    private void Page_Unloaded(object sender, RoutedEventArgs e) =>
        _client.SnapshotReceived -= Client_SnapshotReceived;

    private void Client_SnapshotReceived(object? sender, ServiceSnapshot snapshot) =>
        DispatcherQueue.TryEnqueue(() => Apply(snapshot.Tsf));

    private void Apply(TsfState state)
    {
        ServerText.Text = !state.Enabled ? "未启用" : state.ServerRunning ? "运行中" : "未启动";
        ClientCountText.Text = state.ClientCount.ToString();
        CompositionStateText.Text = state.Composition.Active ? "活动" : "空闲";
        CompositionDetailText.Text = state.Composition.Active
            ? $"{state.Composition.HostProcessName} (PID {state.Composition.HostProcessId}) · revision {state.Composition.Revision} · {state.Composition.Style} · {state.Composition.Processor}"
            : "当前没有由 TIP 接管的任务。";
        StartupErrorText.Text = string.IsNullOrWhiteSpace(state.StartupError)
            ? string.Empty
            : $"启动错误：{state.StartupError}";
        ClientList.ItemsSource = state.Clients.Select(client =>
            $"{client.ProcessName} · PID {client.ProcessId}").ToList();
    }
}
