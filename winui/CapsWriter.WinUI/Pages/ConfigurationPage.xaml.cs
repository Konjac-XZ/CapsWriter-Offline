using System.Collections.ObjectModel;
using CapsWriter_WinUI.Models;
using CapsWriter_WinUI.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Windows.Foundation;

namespace CapsWriter_WinUI.Pages;

public sealed partial class ConfigurationPage : Page
{
    private static readonly TimeSpan AutoSaveDelay = TimeSpan.FromMilliseconds(500);
    private readonly PythonServiceClient _client;
    private readonly DispatcherQueueTimer _asrPromptSaveTimer;
    private readonly DispatcherQueueTimer _llmPromptSaveTimer;
    private readonly DispatcherQueueTimer _lexiconSaveTimer;
    private readonly DispatcherQueueTimer _historyRefreshTimer;
    private readonly SemaphoreSlim _configurationSaveLock = new(1, 1);
    private string? _providerId;
    private string _savedAsrPrompt = string.Empty;
    private string _savedLlmPrompt = string.Empty;
    private string _savedLexicon = string.Empty;
    private bool _loading;
    private bool _loadingHistory;
    private bool _subscribed;

    public ObservableCollection<string> PolishHistoryItems { get; } = [];

    public ConfigurationPage()
    {
        InitializeComponent();
        _client = ((App)Application.Current).ServiceClient;
        _asrPromptSaveTimer = CreateAutoSaveTimer(AsrPromptSaveTimer_Tick);
        _llmPromptSaveTimer = CreateAutoSaveTimer(LlmPromptSaveTimer_Tick);
        _lexiconSaveTimer = CreateAutoSaveTimer(LexiconSaveTimer_Tick);
        _historyRefreshTimer = DispatcherQueue.CreateTimer();
        _historyRefreshTimer.Interval = TimeSpan.FromSeconds(2);
        _historyRefreshTimer.IsRepeating = true;
        _historyRefreshTimer.Tick += HistoryRefreshTimer_Tick;
        PolishHistoryList.ItemsSource = PolishHistoryItems;
    }

    private DispatcherQueueTimer CreateAutoSaveTimer(
        TypedEventHandler<DispatcherQueueTimer, object> tickHandler)
    {
        DispatcherQueueTimer timer = DispatcherQueue.CreateTimer();
        timer.Interval = AutoSaveDelay;
        timer.IsRepeating = false;
        timer.Tick += tickHandler;
        return timer;
    }

    private async void Page_Loaded(object sender, RoutedEventArgs e)
    {
        if (!_subscribed)
        {
            _client.ContextSettingChanged += Client_ContextSettingChanged;
            _subscribed = true;
        }
        await LoadConfigurationAsync();
        await RefreshPolishHistoryAsync();
        _historyRefreshTimer.Start();
    }

    private void Page_Unloaded(object sender, RoutedEventArgs e)
    {
        _historyRefreshTimer.Stop();
        if (!_subscribed)
        {
            return;
        }
        _client.ContextSettingChanged -= Client_ContextSettingChanged;
        _subscribed = false;
    }

    private void Client_ContextSettingChanged(object? sender, ContextSettingChangedEvent change)
    {
        if (change.Target != "textbox_context")
        {
            return;
        }
        DispatcherQueue.TryEnqueue(() =>
        {
            _loading = true;
            TextboxToggle.IsOn = change.Enabled;
            _loading = false;
            ShowStatus(
                change.Enabled ? "热键已启用当前文本框上下文。" : "热键已关闭当前文本框上下文。",
                InfoBarSeverity.Informational);
        });
    }

    private async Task LoadConfigurationAsync()
    {
        try
        {
            _loading = true;
            StopEditorSaveTimers();
            ShowStatus("正在读取配置…", InfoBarSeverity.Informational, true);
            ConfigurationState state = await _client.GetConfigurationAsync();
            _providerId = state.ProviderId;
            AsrPromptTitle.Text = state.ProviderName is null
                ? "ASR Prompt"
                : $"ASR Prompt · {state.ProviderName}";
            AsrPromptBox.Text = state.AsrPrompt;
            LlmPromptBox.Text = state.LlmPrompt;
            HistoryToggle.IsOn = state.HistoryContextEnabled;
            TextboxToggle.IsOn = state.TextboxContextEnabled;
            TextboxStateToggle.IsOn = state.ActiveTextboxStateEnabled;
            VisionToggle.IsOn = state.VisionContextEnabled;
            LexiconBox.Text = state.LexiconText;
            // TextBox can normalize multiline text (notably line endings). Use the
            // value read back from the control as the clean baseline so merely
            // loading the page cannot look like a user edit.
            _savedAsrPrompt = AsrPromptBox.Text;
            _savedLlmPrompt = LlmPromptBox.Text;
            _savedLexicon = LexiconBox.Text;
            StatusBar.IsOpen = false;
        }
        catch (Exception exception)
        {
            ShowStatus($"读取配置失败：{exception.Message}", InfoBarSeverity.Error);
        }
        finally
        {
            _loading = false;
        }
    }

    private async void Reload_Click(object sender, RoutedEventArgs e) => await LoadConfigurationAsync();

    private void AsrPromptBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        UpdateAutoSaveTimer(AsrPromptBox, _savedAsrPrompt, _asrPromptSaveTimer);
    }

    private void LlmPromptBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        UpdateAutoSaveTimer(LlmPromptBox, _savedLlmPrompt, _llmPromptSaveTimer);
    }

    private void LexiconBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        UpdateAutoSaveTimer(LexiconBox, _savedLexicon, _lexiconSaveTimer);
    }

    private void UpdateAutoSaveTimer(
        TextBox textBox,
        string savedText,
        DispatcherQueueTimer timer)
    {
        timer.Stop();
        if (_loading || textBox.Text == savedText)
        {
            return;
        }
        timer.Start();
    }

    private void RestartAutoSaveTimer(DispatcherQueueTimer timer)
    {
        if (_loading)
        {
            return;
        }
        timer.Stop();
        timer.Start();
    }

    private void StopEditorSaveTimers()
    {
        _asrPromptSaveTimer.Stop();
        _llmPromptSaveTimer.Stop();
        _lexiconSaveTimer.Stop();
    }

    private void EditableTextBox_CopyingToClipboard(
        TextBox sender,
        TextControlCopyingToClipboardEventArgs args)
    {
        args.Handled = true;
        CopySelectionToClipboard(sender, cut: false);
    }

    private void EditableTextBox_CuttingToClipboard(
        TextBox sender,
        TextControlCuttingToClipboardEventArgs args)
    {
        args.Handled = true;
        CopySelectionToClipboard(sender, cut: true);
    }

    private void CopySelectionToClipboard(TextBox textBox, bool cut)
    {
        string operation = cut ? "cut" : "copy";
        string editor = GetEditorLogName(textBox);
        int selectionLength = textBox.SelectionLength;
        _client.ReportLocalLog(
            $"剪贴板命令触发：operation={operation} editor={editor} selection_length={selectionLength}",
            "#666666");
        if (selectionLength <= 0)
        {
            return;
        }

        string selectedText = textBox.SelectedText;
        try
        {
            DataPackage dataPackage = new();
            dataPackage.SetText(selectedText);
            Clipboard.SetContent(dataPackage);
            try
            {
                Clipboard.Flush();
            }
            catch (Exception exception)
            {
                // SetContent already completed. Flush only detaches the data from
                // this process so it survives application shutdown; a Flush
                // failure must not turn a usable copy into a failed cut.
                _client.ReportLocalLog(
                    "剪贴板持久化警告："
                        + DescribeClipboardException("flush", exception),
                    "#CA5010");
            }
            if (cut)
            {
                textBox.SelectedText = string.Empty;
            }
            _client.ReportLocalLog(
                $"剪贴板写入成功：operation={operation} editor={editor} text_length={selectedText.Length}",
                "#107C10");
        }
        catch (Exception exception)
        {
            string diagnostic = DescribeClipboardException("set_content", exception);
            _client.ReportLocalLog(
                $"剪贴板写入失败：operation={operation} editor={editor} {diagnostic}",
                "#C42B1C");
            ShowStatus(
                $"{(cut ? "剪切" : "复制")}失败：{diagnostic}",
                InfoBarSeverity.Error);
        }
    }

    private static string DescribeClipboardException(string stage, Exception exception)
    {
        string message = string.IsNullOrWhiteSpace(exception.Message)
            ? "（异常未提供消息）"
            : exception.Message;
        return $"stage={stage} exception={exception.GetType().Name} "
            + $"hresult=0x{exception.HResult:X8} message={message}";
    }

    private string GetEditorLogName(TextBox textBox) =>
        ReferenceEquals(textBox, AsrPromptBox)
            ? "asr_prompt"
            : ReferenceEquals(textBox, LlmPromptBox)
                ? "llm_prompt"
                : ReferenceEquals(textBox, LexiconBox)
                    ? "lexicon"
                    : "unknown";

    private async void EditableTextBox_LostFocus(object sender, RoutedEventArgs e)
    {
        if (_loading || sender is not TextBox textBox)
        {
            return;
        }
        if (ReferenceEquals(textBox, AsrPromptBox))
        {
            _asrPromptSaveTimer.Stop();
            await SaveAsrPromptAsync();
        }
        else if (ReferenceEquals(textBox, LlmPromptBox))
        {
            _llmPromptSaveTimer.Stop();
            await SaveLlmPromptAsync();
        }
        else if (ReferenceEquals(textBox, LexiconBox))
        {
            _lexiconSaveTimer.Stop();
            await SaveLexiconAsync();
        }
    }

    private async void AsrPromptSaveTimer_Tick(DispatcherQueueTimer sender, object args) =>
        await SaveAsrPromptAsync();

    private async void LlmPromptSaveTimer_Tick(DispatcherQueueTimer sender, object args) =>
        await SaveLlmPromptAsync();

    private async void LexiconSaveTimer_Tick(DispatcherQueueTimer sender, object args) =>
        await SaveLexiconAsync();

    private async void HistoryRefreshTimer_Tick(DispatcherQueueTimer sender, object args) =>
        await RefreshPolishHistoryAsync();

    private async Task RefreshPolishHistoryAsync()
    {
        if (_loadingHistory)
        {
            return;
        }
        _loadingHistory = true;
        try
        {
            IReadOnlyList<string> items = (await _client.GetPolishHistoryAsync())
                .Reverse()
                .ToArray();
            if (!PolishHistoryItems.SequenceEqual(items))
            {
                PolishHistoryItems.Clear();
                foreach (string item in items)
                {
                    PolishHistoryItems.Add(item);
                }
            }
            bool hasItems = PolishHistoryItems.Count > 0;
            PolishHistoryList.Visibility = hasItems
                ? Visibility.Visible
                : Visibility.Collapsed;
            EmptyPolishHistoryText.Text = HistoryToggle.IsOn ? "暂无内容" : "已关闭";
            EmptyPolishHistoryText.Visibility = hasItems
                ? Visibility.Collapsed
                : Visibility.Visible;
        }
        catch
        {
            if (PolishHistoryItems.Count == 0)
            {
                PolishHistoryList.Visibility = Visibility.Collapsed;
                EmptyPolishHistoryText.Text = "读取失败";
                EmptyPolishHistoryText.Visibility = Visibility.Visible;
            }
        }
        finally
        {
            _loadingHistory = false;
        }
    }

    private async Task SaveAsrPromptAsync()
    {
        string text = AsrPromptBox.Text;
        if (text == _savedAsrPrompt)
        {
            return;
        }
        if (string.IsNullOrWhiteSpace(_providerId))
        {
            ShowStatus("当前没有可保存 Prompt 的转录服务商。", InfoBarSeverity.Warning);
            return;
        }
        await _configurationSaveLock.WaitAsync();
        try
        {
            if (text == _savedAsrPrompt)
            {
                return;
            }
            await _client.SetAsrPromptAsync(_providerId, text);
            _savedAsrPrompt = text;
            ShowStatus("ASR Prompt 已自动保存。", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowStatus($"保存 ASR Prompt 失败：{exception.Message}", InfoBarSeverity.Error);
        }
        finally
        {
            _configurationSaveLock.Release();
            if (text != AsrPromptBox.Text)
            {
                RestartAutoSaveTimer(_asrPromptSaveTimer);
            }
        }
    }

    private async Task SaveLlmPromptAsync()
    {
        string text = LlmPromptBox.Text;
        if (text == _savedLlmPrompt)
        {
            return;
        }
        await _configurationSaveLock.WaitAsync();
        try
        {
            if (text == _savedLlmPrompt)
            {
                return;
            }
            await _client.SetLlmPromptAsync(text);
            _savedLlmPrompt = text;
            ShowStatus("LLM Prompt 已自动保存。", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowStatus($"保存 LLM Prompt 失败：{exception.Message}", InfoBarSeverity.Error);
        }
        finally
        {
            _configurationSaveLock.Release();
            if (text != LlmPromptBox.Text)
            {
                RestartAutoSaveTimer(_llmPromptSaveTimer);
            }
        }
    }

    private async void ContextToggle_Toggled(object sender, RoutedEventArgs e)
    {
        if (_loading || sender is not ToggleSwitch toggle)
        {
            return;
        }
        string name = ReferenceEquals(toggle, HistoryToggle)
            ? "history"
            : ReferenceEquals(toggle, TextboxToggle)
                ? "textbox"
                : ReferenceEquals(toggle, TextboxStateToggle)
                    ? "textbox_state"
                    : "vision";
        bool requested = toggle.IsOn;
        _client.ReportLocalLog($"上下文设置请求：{name}={requested}", "#666666");
        try
        {
            System.Text.Json.JsonElement result = await _client.SetContextSettingAsync(
                name,
                requested);
            if (!result.TryGetProperty("enabled", out System.Text.Json.JsonElement value)
                || value.ValueKind is not System.Text.Json.JsonValueKind.True
                    and not System.Text.Json.JsonValueKind.False)
            {
                throw new InvalidDataException("Python 后端未返回有效的配置读回值。");
            }
            bool persisted = value.GetBoolean();
            _loading = true;
            toggle.IsOn = persisted;
            _loading = false;
            if (persisted != requested)
            {
                throw new InvalidDataException(
                    $"配置读回不一致：请求 {requested}，实际 {persisted}。");
            }
            _client.ReportLocalLog($"上下文设置已确认：{name}={persisted}", "#107C10");
            ShowStatus("上下文设置已保存并立即生效。", InfoBarSeverity.Success);
            if (ReferenceEquals(toggle, HistoryToggle))
            {
                await RefreshPolishHistoryAsync();
            }
        }
        catch (Exception exception)
        {
            _loading = false;
            _client.ReportLocalLog(
                $"上下文设置失败：{name}={requested}，{exception.Message}",
                "#C42B1C");
            ShowStatus($"保存上下文设置失败：{exception.Message}", InfoBarSeverity.Error);
            await LoadConfigurationAsync();
        }
    }

    private async Task SaveLexiconAsync()
    {
        string text = LexiconBox.Text;
        if (text == _savedLexicon)
        {
            return;
        }
        await _configurationSaveLock.WaitAsync();
        try
        {
            if (text == _savedLexicon)
            {
                return;
            }
            System.Text.Json.JsonElement result = await _client.SetLexiconAsync(text);
            int count = result.TryGetProperty("entry_count", out System.Text.Json.JsonElement value)
                ? value.GetInt32()
                : 0;
            _savedLexicon = text;
            ShowStatus($"用户词库已自动保存，共 {count} 条。", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowStatus($"保存用户词库失败：{exception.Message}", InfoBarSeverity.Error);
        }
        finally
        {
            _configurationSaveLock.Release();
            if (text != LexiconBox.Text)
            {
                RestartAutoSaveTimer(_lexiconSaveTimer);
            }
        }
    }

    private async void ClearHistory_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            System.Text.Json.JsonElement result = await _client.ClearHistoryAsync();
            int cleared = result.TryGetProperty("cleared", out System.Text.Json.JsonElement count)
                ? count.GetInt32()
                : 0;
            ShowStatus($"已清空 {cleared} 条最近上屏历史。", InfoBarSeverity.Success);
            await RefreshPolishHistoryAsync();
        }
        catch (Exception exception)
        {
            ShowStatus($"清空最近上屏历史失败：{exception.Message}", InfoBarSeverity.Error);
        }
    }

    private void ShowStatus(string message, InfoBarSeverity severity, bool progress = false)
    {
        StatusBar.Message = message;
        StatusBar.Severity = severity;
        StatusBar.IsOpen = true;
        StatusBar.IsIconVisible = !progress;
    }
}
