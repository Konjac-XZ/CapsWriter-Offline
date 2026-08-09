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
    private bool _mutatingPreferences;

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

    private async void ClearPersonalization_Click(object sender, RoutedEventArgs e)
    {
        if (_mutatingPreferences)
        {
            return;
        }
        ContentDialog confirmation = new()
        {
            XamlRoot = XamlRoot,
            Title = "清空全部学习数据？",
            Content = "这会删除修正队列、已学习偏好和反思运行记录；最近上屏内容、每日统计和任务约束不会受影响。",
            PrimaryButtonText = "清空",
            CloseButtonText = "取消",
            DefaultButton = ContentDialogButton.Close,
        };
        if (await confirmation.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunPreferenceMutationAsync(
            () => _client.ClearPersonalizationAsync(),
            "清空学习数据失败");
    }

    private async void DeletePreference_Click(object sender, RoutedEventArgs e)
    {
        LearnedPreferenceState? preference = FindPreference(sender);
        if (preference is null || _mutatingPreferences)
        {
            return;
        }
        ContentDialog confirmation = new()
        {
            XamlRoot = XamlRoot,
            Title = "删除这条偏好？",
            Content = preference.PreferredValue,
            PrimaryButtonText = "删除",
            CloseButtonText = "取消",
            DefaultButton = ContentDialogButton.Close,
        };
        if (await confirmation.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }
        await RunPreferenceMutationAsync(
            () => _client.DeleteLearnedPreferenceAsync(preference.Id),
            "删除偏好失败");
    }

    private async void EditPreference_Click(object sender, RoutedEventArgs e)
    {
        LearnedPreferenceState? preference = FindPreference(sender);
        if (preference is null || _mutatingPreferences)
        {
            return;
        }

        TextBox preferredValueBox = new()
        {
            Header = "首选内容",
            Text = preference.PreferredValue,
            MaxLength = 200,
        };
        TextBox avoidValuesBox = new()
        {
            Header = "避免内容（每行一项）",
            Text = string.Join(Environment.NewLine, preference.AvoidValues),
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 72,
        };
        TextBox keywordsBox = new()
        {
            Header = "检索触发词（每行一项）",
            Text = string.Join(Environment.NewLine, preference.Keywords),
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            MinHeight = 72,
        };
        ComboBox kindBox = BuildOptionBox(
            "类型",
            new (string Value, string Label)[]
            {
                ("terminology", "术语"),
                ("spelling", "拼写"),
                ("casing", "大小写"),
                ("punctuation", "标点"),
                ("formatting", "格式"),
                ("style", "风格"),
                ("avoidance", "规避"),
            },
            preference.Kind);
        ComboBox statusBox = BuildOptionBox(
            "状态",
            new (string Value, string Label)[]
            {
                ("active", "生效"),
                ("candidate", "候选"),
                ("superseded", "已替代"),
            },
            preference.Status);
        StackPanel editor = new() { Spacing = 10, MinWidth = 440 };
        editor.Children.Add(preferredValueBox);
        editor.Children.Add(kindBox);
        editor.Children.Add(statusBox);
        editor.Children.Add(avoidValuesBox);
        editor.Children.Add(keywordsBox);

        ContentDialog dialog = new()
        {
            XamlRoot = XamlRoot,
            Title = "编辑学习偏好",
            Content = editor,
            PrimaryButtonText = "保存",
            CloseButtonText = "取消",
            DefaultButton = ContentDialogButton.Primary,
        };
        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }

        LearnedPreferenceState updated = new()
        {
            Id = preference.Id,
            Kind = SelectedOption(kindBox),
            PreferredValue = preferredValueBox.Text.Trim(),
            AvoidValues = ParseLines(avoidValuesBox.Text),
            Keywords = ParseLines(keywordsBox.Text),
            Status = SelectedOption(statusBox),
        };
        await RunPreferenceMutationAsync(
            () => _client.UpdateLearnedPreferenceAsync(updated),
            "保存偏好失败");
    }

    private async Task RunPreferenceMutationAsync(
        Func<Task<System.Text.Json.JsonElement>> operation,
        string errorTitle)
    {
        _mutatingPreferences = true;
        PreferencesProgress.IsActive = true;
        try
        {
            await operation();
            await LoadPreferencesAsync();
        }
        catch (Exception exception)
        {
            ContentDialog error = new()
            {
                XamlRoot = XamlRoot,
                Title = errorTitle,
                Content = exception.Message,
                CloseButtonText = "关闭",
            };
            await error.ShowAsync();
        }
        finally
        {
            PreferencesProgress.IsActive = false;
            _mutatingPreferences = false;
        }
    }

    private LearnedPreferenceState? FindPreference(object sender)
    {
        if (sender is not Button button || button.Tag is null)
        {
            return null;
        }
        if (!int.TryParse(button.Tag.ToString(), out int id))
        {
            return null;
        }
        return _allPreferences.FirstOrDefault(preference => preference.Id == id);
    }

    private static ComboBox BuildOptionBox(
        string header,
        IEnumerable<(string Value, string Label)> options,
        string selectedValue)
    {
        ComboBox comboBox = new() { Header = header, HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach ((string value, string label) in options)
        {
            comboBox.Items.Add(new ComboBoxItem { Content = label, Tag = value });
        }
        comboBox.SelectedItem = comboBox.Items
            .OfType<ComboBoxItem>()
            .FirstOrDefault(item => string.Equals(item.Tag?.ToString(), selectedValue, StringComparison.Ordinal))
            ?? comboBox.Items[0];
        return comboBox;
    }

    private static string SelectedOption(ComboBox comboBox) =>
        (comboBox.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? string.Empty;

    private static List<string> ParseLines(string text) =>
        text.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries)
            .Select(value => value.Trim())
            .Where(value => value.Length > 0)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToList();

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
