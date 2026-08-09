using CapsWriter_WinUI.Models;
using CapsWriter_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CapsWriter_WinUI.Pages;

public sealed partial class ReflectionPage : Page
{
    private readonly PythonServiceClient _client;

    public ReflectionPage()
    {
        InitializeComponent();
        _client = ((App)Application.Current).ServiceClient;
    }

    private void Page_Loaded(object sender, RoutedEventArgs e)
    {
        _client.SnapshotReceived += Client_SnapshotReceived;
        if (_client.LastSnapshot is { } snapshot) Apply(snapshot.Reflection);
    }

    private void Page_Unloaded(object sender, RoutedEventArgs e) =>
        _client.SnapshotReceived -= Client_SnapshotReceived;

    private void Client_SnapshotReceived(object? sender, ServiceSnapshot snapshot) =>
        DispatcherQueue.TryEnqueue(() => Apply(snapshot.Reflection));

    private void Apply(ReflectionState state)
    {
        PhaseText.Text = state.Enabled ? state.Phase : "disabled";
        PendingText.Text = (state.Storage?.Corrections.Pending ?? 0).ToString();
        PreferenceText.Text = (state.Storage?.Preferences.Values.Sum() ?? 0).ToString();
        ReflectionRunState? run = state.Storage?.LatestRun;
        LatestRunText.Text = run is null
            ? "还没有反思运行记录。"
            : $"{run.Outcome} · {run.Provider} / {run.Model} · {run.EventCount} 条事件";
        RuntimeDetailText.Text = $"轮询 {state.Phase}；当前批次 {state.ActiveEventCount} 条。"
            + (string.IsNullOrWhiteSpace(state.LastErrorType) ? string.Empty : $" 最近错误：{state.LastErrorType}");
    }
}
