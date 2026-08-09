using System.Collections.ObjectModel;
using CapsWriter_WinUI.Models;
using CapsWriter_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CapsWriter_WinUI.Pages;

public sealed partial class ReflectionPage : Page
{
    private readonly PythonServiceClient _client;
    private IReadOnlyList<LearnedPreferenceState> _allPreferences = [];
    private bool _subscribed;
    private bool _loadingPreferences;

    public ObservableCollection<LearnedPreferenceState> Preferences { get; } = [];

    public ReflectionPage()
    {
        InitializeComponent();
        PreferencesList.ItemsSource = Preferences;
        _client = ((App)Application.Current).ServiceClient;
        StatusFilter.SelectedIndex = 0;
    }

    private async void Page_Loaded(object sender, RoutedEventArgs e)
    {
        if (!_subscribed)
        {
            _client.SnapshotReceived += Client_SnapshotReceived;
            _subscribed = true;
        }
        if (_client.LastSnapshot is { } snapshot)
        {
            Apply(snapshot.Reflection);
        }
        await LoadPreferencesAsync();
    }

    private void Page_Unloaded(object sender, RoutedEventArgs e)
    {
        if (!_subscribed)
        {
            return;
        }
        _client.SnapshotReceived -= Client_SnapshotReceived;
        _subscribed = false;
    }

    private void Client_SnapshotReceived(object? sender, ServiceSnapshot snapshot) =>
        DispatcherQueue.TryEnqueue(() => Apply(snapshot.Reflection));

    private void Apply(ReflectionState state)
    {
        PhaseText.Text = state.Enabled ? FormatPhase(state.Phase) : "已关闭";
        PendingText.Text = (state.Storage?.Corrections.Pending ?? 0).ToString();
        PreferenceText.Text = (state.Storage?.Preferences.Values.Sum() ?? 0).ToString();
        ReflectionRunState? run = state.Storage?.LatestRun;
        LatestRunText.Text = run is null
            ? "还没有反思运行记录"
            : $"{FormatOutcome(run.Outcome)} · {run.Provider} / {run.Model} · {run.EventCount} 条事件";
        RuntimeDetailText.Text = $"当前批次 {state.ActiveEventCount} 条"
            + (string.IsNullOrWhiteSpace(state.LastErrorType) ? string.Empty : $" · 最近错误 {state.LastErrorType}");
    }

    private async void ReloadPreferences_Click(object sender, RoutedEventArgs e) =>
        await LoadPreferencesAsync();

    private async Task LoadPreferencesAsync()
    {
        if (_loadingPreferences)
        {
            return;
        }
        _loadingPreferences = true;
        PreferencesProgress.IsActive = true;
        EmptyPreferencesText.Visibility = Visibility.Collapsed;
        try
        {
            LearnedPreferencesResult result = await _client.GetLearnedPreferencesAsync();
            _allPreferences = result.Items;
            ApplyPreferenceFilter();
            PreferenceText.Text = result.Total.ToString();
        }
        catch (Exception exception)
        {
            Preferences.Clear();
            DisplayedPreferenceCountText.Text = string.Empty;
            EmptyPreferencesText.Text = $"读取偏好失败：{exception.Message}";
            EmptyPreferencesText.Visibility = Visibility.Visible;
        }
        finally
        {
            PreferencesProgress.IsActive = false;
            _loadingPreferences = false;
        }
    }

    private void StatusFilter_SelectionChanged(object sender, SelectionChangedEventArgs e) =>
        ApplyPreferenceFilter();

    private void ApplyPreferenceFilter()
    {
        string status = StatusFilter.SelectedItem is ComboBoxItem item
            ? item.Tag?.ToString() ?? "all"
            : "all";
        IEnumerable<LearnedPreferenceState> visible = status == "all"
            ? _allPreferences
            : _allPreferences.Where(preference => preference.Status == status);
        Preferences.Clear();
        foreach (LearnedPreferenceState preference in visible)
        {
            Preferences.Add(preference);
        }
        DisplayedPreferenceCountText.Text = status == "all"
            ? Preferences.Count.ToString()
            : $"{Preferences.Count} / {_allPreferences.Count}";
        EmptyPreferencesText.Text = "还没有学习到偏好";
        EmptyPreferencesText.Visibility = Preferences.Count == 0
            ? Visibility.Visible
            : Visibility.Collapsed;
    }

    private static string FormatPhase(string phase) => phase switch
    {
        "starting" => "正在启动",
        "disabled" => "已关闭",
        "waiting_for_idle" => "等待空闲",
        "checking" => "检查修正",
        "processing" => "正在学习",
        "waiting_for_provider" => "等待模型",
        "error_waiting_retry" => "等待重试",
        "idle" => "空闲",
        _ => phase,
    };

    private static string FormatOutcome(string outcome) => outcome switch
    {
        "success" => "成功",
        "failure" => "失败",
        _ => outcome,
    };
}
