using CapsWriter_WinUI.Models;
using CapsWriter_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CapsWriter_WinUI.Pages;

public sealed partial class DiagnosticsPage : Page
{
    private readonly PythonServiceClient _client;

    public DiagnosticsPage()
    {
        InitializeComponent();
        _client = ((App)Application.Current).ServiceClient;
    }

    private void Page_Loaded(object sender, RoutedEventArgs e)
    {
        _client.SnapshotReceived += Client_SnapshotReceived;
        _client.ConnectionStateChanged += Client_ConnectionStateChanged;
        ApplyConnection(_client.ConnectionState);
        if (_client.LastSnapshot is { } snapshot)
        {
            ApplySnapshot(snapshot);
        }
        RootText.Text = RepositoryLocator.FindRoot() ?? "找不到仓库根目录";
    }

    private void Page_Unloaded(object sender, RoutedEventArgs e)
    {
        _client.SnapshotReceived -= Client_SnapshotReceived;
        _client.ConnectionStateChanged -= Client_ConnectionStateChanged;
    }

    private void Client_SnapshotReceived(object? sender, ServiceSnapshot snapshot) =>
        DispatcherQueue.TryEnqueue(() => ApplySnapshot(snapshot));

    private void Client_ConnectionStateChanged(object? sender, string state) =>
        DispatcherQueue.TryEnqueue(() => ApplyConnection(state));

    private void ApplyConnection(string state)
    {
        ConnectionText.Text = state;
        LogCountText.Text = $"{_client.RecentLogs.Count} / 500";
    }

    private void ApplySnapshot(ServiceSnapshot snapshot)
    {
        ProtocolText.Text = $"v{snapshot.ProtocolVersion}";
        ProcessText.Text = $"Python PID {snapshot.Service.ProcessId} · "
            + (snapshot.Service.Recording ? "正在录音" : snapshot.Service.Transcribing ? "正在处理" : "空闲");
        LogCountText.Text = $"{_client.RecentLogs.Count} / 500";
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            await _client.RequestSnapshotAsync();
        }
        catch (Exception exception)
        {
            ConnectionText.Text = $"刷新失败：{exception.Message}";
        }
    }
}
