using CapsWriter_WinUI.Models;
using CapsWriter_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CapsWriter_WinUI.Pages;

public sealed partial class ConfigurationPage : Page
{
    private readonly PythonServiceClient _client;
    private string? _providerId;
    private bool _loading;
    private bool _subscribed;

    public ConfigurationPage()
    {
        InitializeComponent();
        _client = ((App)Application.Current).ServiceClient;
    }

    private async void Page_Loaded(object sender, RoutedEventArgs e)
    {
        if (!_subscribed)
        {
            _client.ContextSettingChanged += Client_ContextSettingChanged;
            _subscribed = true;
        }
        await LoadConfigurationAsync();
    }

    private void Page_Unloaded(object sender, RoutedEventArgs e)
    {
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
            ShowStatus("正在读取配置…", InfoBarSeverity.Informational, true);
            ConfigurationState state = await _client.GetConfigurationAsync();
            _providerId = state.ProviderId;
            ProviderNameText.Text = state.ProviderName is null
                ? "当前没有可用的转录服务商"
                : $"当前服务商：{state.ProviderName}";
            AsrPromptBox.Text = state.AsrPrompt;
            LlmPromptBox.Text = state.LlmPrompt;
            LlmStateText.Text = state.LlmEnabled ? "LLM 润色已启用" : "LLM 润色当前关闭";
            HistoryToggle.IsOn = state.HistoryContextEnabled;
            TextboxToggle.IsOn = state.TextboxContextEnabled;
            VisionToggle.IsOn = state.VisionContextEnabled;
            LexiconBox.Text = state.LexiconText;
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

    private async void SaveAsrPrompt_Click(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(_providerId))
        {
            ShowStatus("当前没有可保存 Prompt 的转录服务商。", InfoBarSeverity.Warning);
            return;
        }
        try
        {
            await _client.SetAsrPromptAsync(_providerId, AsrPromptBox.Text);
            ShowStatus("ASR Prompt 已保存，将从下一次录音开始生效。", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowStatus($"保存 ASR Prompt 失败：{exception.Message}", InfoBarSeverity.Error);
        }
    }

    private async void SaveLlmPrompt_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            await _client.SetLlmPromptAsync(LlmPromptBox.Text);
            ShowStatus("LLM Prompt 已保存并立即生效。", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowStatus($"保存 LLM Prompt 失败：{exception.Message}", InfoBarSeverity.Error);
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
                : "vision";
        try
        {
            await _client.SetContextSettingAsync(name, toggle.IsOn);
            ShowStatus("上下文设置已保存并立即生效。", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowStatus($"保存上下文设置失败：{exception.Message}", InfoBarSeverity.Error);
            await LoadConfigurationAsync();
        }
    }

    private async void SaveLexicon_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            System.Text.Json.JsonElement result = await _client.SetLexiconAsync(LexiconBox.Text);
            int count = result.TryGetProperty("entry_count", out System.Text.Json.JsonElement value)
                ? value.GetInt32()
                : 0;
            ShowStatus($"用户词库已保存，共 {count} 条。", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowStatus($"保存用户词库失败：{exception.Message}", InfoBarSeverity.Error);
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
