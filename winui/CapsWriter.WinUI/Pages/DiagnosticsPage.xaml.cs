using System.Diagnostics;
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

    private void OpenRepositoryInVsCode_Click(object sender, RoutedEventArgs e)
    {
        string? root = RepositoryLocator.FindRoot();
        if (root is null)
        {
            ShowStatus("找不到仓库根目录，无法使用 VS Code 打开。", InfoBarSeverity.Error);
            return;
        }
        LaunchExternalTool("Code.exe", root, root, "VS Code");
    }

    private void OpenPolishConfigInNotepadPlusPlus_Click(object sender, RoutedEventArgs e)
    {
        string? root = RepositoryLocator.FindRoot();
        if (root is null)
        {
            ShowStatus("找不到仓库根目录，无法定位 polish.yaml。", InfoBarSeverity.Error);
            return;
        }

        string polishConfigPath = Path.Combine(root, "config", "polish", "polish.yaml");
        if (!File.Exists(polishConfigPath))
        {
            ShowStatus($"找不到配置文件：{polishConfigPath}", InfoBarSeverity.Error);
            return;
        }
        LaunchExternalTool("notepad++.exe", polishConfigPath, root, "Notepad++");
    }

    private void OpenRuntimeLogInNotepadPlusPlus_Click(object sender, RoutedEventArgs e)
    {
        string? logDirectory = Environment.GetEnvironmentVariable("CAPSWRITER_LOG_DIR");
        if (string.IsNullOrWhiteSpace(logDirectory))
        {
            string localAppData = Environment.GetFolderPath(
                Environment.SpecialFolder.LocalApplicationData);
            logDirectory = Path.Combine(localAppData, "CapsWriter-Offline", "Logs");
        }

        string logPath = Path.Combine(logDirectory, "capswriter.log");
        if (!File.Exists(logPath))
        {
            ShowStatus($"找不到当前日志：{logPath}", InfoBarSeverity.Error);
            return;
        }
        LaunchExternalTool("notepad++.exe", logPath, logDirectory, "Notepad++");
    }

    private void LaunchExternalTool(
        string executable,
        string target,
        string workingDirectory,
        string displayName)
    {
        try
        {
            ProcessStartInfo startInfo = new()
            {
                FileName = executable,
                WorkingDirectory = workingDirectory,
                UseShellExecute = true,
            };
            startInfo.ArgumentList.Add(target);
            Process.Start(startInfo);
            ShowStatus($"已交给 {displayName} 打开。", InfoBarSeverity.Success);
        }
        catch (Exception exception)
        {
            ShowStatus(
                $"启动 {displayName} 失败：{exception.Message}",
                InfoBarSeverity.Error);
        }
    }

    private void ShowStatus(string message, InfoBarSeverity severity)
    {
        StatusBar.Message = message;
        StatusBar.Severity = severity;
        StatusBar.IsOpen = true;
    }
}
