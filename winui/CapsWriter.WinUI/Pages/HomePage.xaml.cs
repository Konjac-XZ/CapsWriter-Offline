using System.Collections.ObjectModel;
using CapsWriter_WinUI.Models;
using CapsWriter_WinUI.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;

namespace CapsWriter_WinUI.Pages;

public sealed partial class HomePage : Page
{
    private const string PauseGlyph = "\uE769";
    private const string PlayGlyph = "\uE768";
    private readonly PythonServiceClient _client;
    private readonly DispatcherQueueTimer _constraintTimer;
    private readonly LatestAudioPlayer _audioPlayer;
    private bool _subscribed;
    private bool _applyingSnapshot;
    private bool _constraintDirty;

    public ObservableCollection<LogEntry> LogEntries { get; } = [];

    public HomePage()
    {
        InitializeComponent();
        _client = ((App)Application.Current).ServiceClient;
        _audioPlayer = ((App)Application.Current).AudioPlayer;
        _constraintTimer = DispatcherQueue.CreateTimer();
        _constraintTimer.Interval = TimeSpan.FromMilliseconds(400);
        _constraintTimer.IsRepeating = false;
        _constraintTimer.Tick += ConstraintTimer_Tick;
    }

    private void Page_Loaded(object sender, RoutedEventArgs e)
    {
        if (!_subscribed)
        {
            _client.LogReceived += Client_LogReceived;
            _client.SnapshotReceived += Client_SnapshotReceived;
            _client.ConnectionStateChanged += Client_ConnectionStateChanged;
            _client.DailyInputCountChanged += Client_DailyInputCountChanged;
            _audioPlayer.PlaybackStateChanged += AudioPlayer_PlaybackStateChanged;
            _audioPlayer.PlaybackFailed += AudioPlayer_PlaybackFailed;
            _subscribed = true;
        }
        ConnectionStateText.Text = _client.ConnectionState;
        foreach (LogEntry entry in _client.RecentLogs)
        {
            AddLogEntry(entry);
        }
        if (_client.LastSnapshot is { } snapshot)
        {
            ApplySnapshot(snapshot);
        }
    }

    private void Page_Unloaded(object sender, RoutedEventArgs e)
    {
        if (!_subscribed)
        {
            return;
        }
        _client.LogReceived -= Client_LogReceived;
        _client.SnapshotReceived -= Client_SnapshotReceived;
        _client.ConnectionStateChanged -= Client_ConnectionStateChanged;
        _client.DailyInputCountChanged -= Client_DailyInputCountChanged;
        _audioPlayer.PlaybackStateChanged -= AudioPlayer_PlaybackStateChanged;
        _audioPlayer.PlaybackFailed -= AudioPlayer_PlaybackFailed;
        _subscribed = false;
    }

    private void Client_LogReceived(object? sender, LogEntry entry) =>
        DispatcherQueue.TryEnqueue(() => AddLogEntry(entry));

    private void Client_SnapshotReceived(object? sender, ServiceSnapshot snapshot) =>
        DispatcherQueue.TryEnqueue(() => ApplySnapshot(snapshot));

    private void Client_ConnectionStateChanged(object? sender, string state) =>
        DispatcherQueue.TryEnqueue(() => ConnectionStateText.Text = state);

    private void Client_DailyInputCountChanged(object? sender, int count) =>
        DispatcherQueue.TryEnqueue(() => SetDailyInputCount(count));

    private void AudioPlayer_PlaybackStateChanged(object? sender, bool playing) =>
        DispatcherQueue.TryEnqueue(() =>
        {
            PlaybackIcon.Glyph = playing ? PauseGlyph : PlayGlyph;
            ToolTipService.SetToolTip(PlaybackButton, playing ? "停止播放" : "播放最近录音");
        });

    private void AudioPlayer_PlaybackFailed(object? sender, string message) =>
        DispatcherQueue.TryEnqueue(() =>
            AddLogEntry(new LogEntry(DateTimeOffset.Now, $"播放失败：{message}", "#C42B1C")));

    private void AddLogEntry(LogEntry entry)
    {
        if (LogEntries.Count >= 500)
        {
            LogEntries.RemoveAt(0);
        }
        LogEntries.Add(entry);
        LogList.ScrollIntoView(entry);
    }

    private void ApplySnapshot(ServiceSnapshot snapshot)
    {
        _applyingSnapshot = true;
        try
        {
            ConnectionStateText.Text = snapshot.Service.Recording
                ? "正在录音"
                : snapshot.Service.Transcribing
                    ? "正在处理"
                    : "运行中";
            RetryLatestButton.IsEnabled = !snapshot.Service.Recording
                && !snapshot.Service.Transcribing;
            AbandonCurrentButton.IsEnabled = snapshot.Service.Recording
                || snapshot.Service.Transcribing
                || !string.IsNullOrWhiteSpace(snapshot.Service.ActiveTaskId);

            string? selectedKey = (ModelComboBox.SelectedItem as ModelState)?.Key;
            ModelComboBox.ItemsSource = snapshot.Models;
            UpdateModelDisplayMember();
            ModelComboBox.SelectedItem = snapshot.Models.FirstOrDefault(model =>
                model.Key == (selectedKey ?? snapshot.ActiveModel?.Key));
            ModelState? active = snapshot.ActiveModel;
            RealtimeToggle.Visibility = active is not null
                && active.InputModes.Contains("live_audio")
                && active.InputModes.Contains("file_upload")
                ? Visibility.Visible
                : Visibility.Collapsed;
            RealtimeToggle.IsOn = active?.InputMode == "live_audio";
            SetDailyInputCount(snapshot.DailyInputCount);

            if (!_constraintDirty && SessionConstraintBox.FocusState == FocusState.Unfocused)
            {
                SessionConstraintBox.Text = snapshot.SessionConstraint;
                bool hasConstraint = !string.IsNullOrWhiteSpace(snapshot.SessionConstraint);
                SetConstraintState(hasConstraint ? "已生效" : "未设置", hasConstraint);
            }
        }
        finally
        {
            _applyingSnapshot = false;
        }
    }

    private void SessionConstraintBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (_applyingSnapshot)
        {
            return;
        }
        _constraintDirty = true;
        SetConstraintState("待应用");
        _constraintTimer.Stop();
        _constraintTimer.Start();
    }

    private async void ConstraintTimer_Tick(DispatcherQueueTimer sender, object args)
    {
        try
        {
            SetConstraintState("正在应用…");
            await _client.SetSessionConstraintAsync(SessionConstraintBox.Text);
            _constraintDirty = false;
            bool hasConstraint = !string.IsNullOrWhiteSpace(SessionConstraintBox.Text);
            SetConstraintState(hasConstraint ? "已生效" : "未设置", hasConstraint);
        }
        catch (Exception exception)
        {
            SetConstraintState($"应用失败：{exception.Message}");
        }
    }

    private void SetConstraintState(string text, bool isEffective = false)
    {
        ConstraintStateText.Text = text;
        ConstraintStateText.Visibility = isEffective
            ? Visibility.Collapsed
            : Visibility.Visible;
    }

    private void UpdateModelDisplayMember() =>
        ModelComboBox.DisplayMemberPath = ModelComboBox.IsDropDownOpen
            ? nameof(ModelState.Label)
            : nameof(ModelState.ModelName);

    private void ModelComboBox_DropDownOpened(object sender, object args) =>
        UpdateModelDisplayMember();

    private void ModelComboBox_DropDownClosed(object sender, object args) =>
        UpdateModelDisplayMember();

    private void ClearConstraint_Click(object sender, RoutedEventArgs e)
    {
        SessionConstraintBox.Text = string.Empty;
        _constraintTimer.Stop();
        _constraintTimer.Start();
    }

    private void ClearLog_Click(object sender, RoutedEventArgs e) => LogEntries.Clear();

    private void ClearLogAccelerator_Invoked(
        KeyboardAccelerator sender,
        KeyboardAcceleratorInvokedEventArgs args)
    {
        LogEntries.Clear();
        args.Handled = true;
    }

    private void SetDailyInputCount(int count) =>
        DailyInputText.Text = $"今日已输入 {Math.Max(0, count):N0} 字";

    private async void ModelComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_applyingSnapshot || ModelComboBox.SelectedItem is not ModelState model)
        {
            return;
        }
        if (_client.LastSnapshot?.ActiveModel?.Key == model.Key)
        {
            return;
        }
        try
        {
            await _client.SetActiveModelAsync(model);
        }
        catch (Exception exception)
        {
            AddLogEntry(new LogEntry(DateTimeOffset.Now, $"切换模型失败：{exception.Message}"));
        }
    }

    private async void RealtimeToggle_Toggled(object sender, RoutedEventArgs e)
    {
        if (_applyingSnapshot || ModelComboBox.SelectedItem is not ModelState model)
        {
            return;
        }
        try
        {
            await _client.SetModelModeAsync(model, RealtimeToggle.IsOn ? "live_audio" : "file_upload");
        }
        catch (Exception exception)
        {
            AddLogEntry(new LogEntry(DateTimeOffset.Now, $"切换音频模式失败：{exception.Message}"));
        }
    }

    private async void RetryLatest_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            await _client.RetryLatestAsync();
            AddLogEntry(new LogEntry(DateTimeOffset.Now, "已提交最近录音重试。"));
        }
        catch (Exception exception)
        {
            AddLogEntry(new LogEntry(DateTimeOffset.Now, $"重试失败：{exception.Message}"));
        }
    }

    private async void AbandonCurrent_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            await _client.AbandonCurrentAsync();
            AddLogEntry(new LogEntry(DateTimeOffset.Now, "已请求放弃当前任务。"));
        }
        catch (Exception exception)
        {
            AddLogEntry(new LogEntry(DateTimeOffset.Now, $"放弃任务失败：{exception.Message}"));
        }
    }

    private async void Playback_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            await _audioPlayer.ToggleAsync(_client);
        }
        catch (Exception exception)
        {
            AddLogEntry(new LogEntry(DateTimeOffset.Now, $"播放最近录音失败：{exception.Message}", "#C42B1C"));
        }
    }
}
