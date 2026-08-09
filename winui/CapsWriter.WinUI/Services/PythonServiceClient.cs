using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using System.Text.RegularExpressions;
using CapsWriter_WinUI.Models;

namespace CapsWriter_WinUI.Services;

public sealed partial class PythonServiceClient : IAsyncDisposable
{
    private const string EventMarker = "CW_GUI:";
    private const string CommandMarker = "CW_COMMAND:";
    private const int MaxBufferedLogs = 500;
    private readonly ConcurrentDictionary<string, TaskCompletionSource<JsonElement>> _pending = new();
    private readonly ConcurrentQueue<LogEntry> _logs = new();
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private Process? _process;
    private CancellationTokenSource? _lifetime;

    public event EventHandler<LogEntry>? LogReceived;
    public event EventHandler<ServiceSnapshot>? SnapshotReceived;
    public event EventHandler<StatusOverlayEvent>? StatusOverlayReceived;
    public event EventHandler<int>? DailyInputCountChanged;
    public event EventHandler<ContextSettingChangedEvent>? ContextSettingChanged;
    public event EventHandler<string>? ConnectionStateChanged;

    public ServiceSnapshot? LastSnapshot { get; private set; }
    public string ConnectionState { get; private set; } = "尚未启动";
    public bool IsRunning => _process is { HasExited: false };

    public IReadOnlyList<LogEntry> RecentLogs => _logs.ToArray();

    public void ReportLocalLog(string text, string? color = null) => AddLog(text, color);

    public async Task StartAsync(CancellationToken cancellationToken = default)
    {
        if (IsRunning)
        {
            return;
        }

        string? root = RepositoryLocator.FindRoot();
        if (root is null)
        {
            SetConnectionState("找不到 CapsWriter Python 后端");
            return;
        }

        string python = Path.Combine(root, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python))
        {
            SetConnectionState("找不到 .venv\\Scripts\\python.exe");
            return;
        }

        _lifetime?.Dispose();
        _lifetime = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        ProcessStartInfo startInfo = new()
        {
            FileName = python,
            WorkingDirectory = root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8,
            StandardErrorEncoding = System.Text.Encoding.UTF8,
        };
        startInfo.ArgumentList.Add(Path.Combine(root, "core_client.py"));
        startInfo.Environment["CAPSWRITER_GUI_PROTOCOL"] = "1";
        startInfo.Environment["CAPSWRITER_ROOT"] = root;

        Process process = new() { StartInfo = startInfo, EnableRaisingEvents = true };
        process.Exited += (_, _) =>
        {
            if (!ReferenceEquals(_process, process))
            {
                return;
            }
            SetConnectionState($"Python 后端已退出（{process.ExitCode}）");
            FailPending(new IOException("Python backend exited."));
        };
        if (!process.Start())
        {
            SetConnectionState("Python 后端启动失败");
            process.Dispose();
            return;
        }

        _process = process;
        SetConnectionState("正在连接 Python 后端…");
        _ = ReadOutputAsync(process.StandardOutput, _lifetime.Token);
        _ = ReadErrorAsync(process.StandardError, _lifetime.Token);
        await Task.CompletedTask;
    }

    public Task<JsonElement> RequestSnapshotAsync(CancellationToken cancellationToken = default) =>
        SendCommandAsync("get_snapshot", null, cancellationToken);

    public Task<JsonElement> SetSessionConstraintAsync(string text, CancellationToken cancellationToken = default) =>
        SendCommandAsync("set_session_constraint", new { text }, cancellationToken);

    public Task<JsonElement> SetActiveModelAsync(ModelState model, CancellationToken cancellationToken = default) =>
        SendCommandAsync(
            "set_active_model",
            new { provider_id = model.ProviderId, model_id = model.ModelId },
            cancellationToken);

    public async Task<ConfigurationState> GetConfigurationAsync(CancellationToken cancellationToken = default)
    {
        JsonElement result = await SendCommandAsync("get_configuration", null, cancellationToken);
        return result.Deserialize<ConfigurationState>()
            ?? throw new InvalidDataException("Python 后端返回了无效配置。");
    }

    public async Task<IReadOnlyList<string>> GetPolishHistoryAsync(
        CancellationToken cancellationToken = default)
    {
        JsonElement result = await SendCommandAsync("get_polish_history", null, cancellationToken);
        if (!result.TryGetProperty("items", out JsonElement items)
            || items.ValueKind != JsonValueKind.Array)
        {
            throw new InvalidDataException("Python 后端返回了无效的最近上屏内容。");
        }
        return items.EnumerateArray()
            .Where(item => item.ValueKind == JsonValueKind.String)
            .Select(item => item.GetString() ?? string.Empty)
            .ToArray();
    }

    public async Task<LearnedPreferencesResult> GetLearnedPreferencesAsync(
        CancellationToken cancellationToken = default)
    {
        JsonElement result = await SendCommandAsync(
            "get_learned_preferences",
            new { limit = 500 },
            cancellationToken);
        return result.Deserialize<LearnedPreferencesResult>()
            ?? throw new InvalidDataException("Python 后端返回了无效偏好列表。");
    }

    public async Task<TsfDllInspectionResult> InspectTsfDllVersionsAsync(
        CancellationToken cancellationToken = default)
    {
        JsonElement result = await SendCommandAsync(
            "inspect_tsf_dll_versions",
            null,
            cancellationToken);
        return result.Deserialize<TsfDllInspectionResult>()
            ?? throw new InvalidDataException("Python 后端返回了无效 TSF DLL 检查结果。");
    }

    public Task<JsonElement> SetAsrPromptAsync(
        string providerId,
        string text,
        CancellationToken cancellationToken = default) =>
        SendCommandAsync("set_asr_prompt", new { provider_id = providerId, text }, cancellationToken);

    public Task<JsonElement> SetLlmPromptAsync(string text, CancellationToken cancellationToken = default) =>
        SendCommandAsync("set_llm_prompt", new { text }, cancellationToken);

    public Task<JsonElement> SetContextSettingAsync(
        string name,
        bool enabled,
        CancellationToken cancellationToken = default) =>
        SendCommandAsync("set_context_setting", new { name, enabled }, cancellationToken);

    public Task<JsonElement> SetLexiconAsync(string text, CancellationToken cancellationToken = default) =>
        SendCommandAsync("set_lexicon", new { text }, cancellationToken);

    public Task<JsonElement> SetModelModeAsync(
        ModelState model,
        string mode,
        CancellationToken cancellationToken = default) =>
        SendCommandAsync(
            "set_model_mode",
            new { provider_id = model.ProviderId, model_id = model.ModelId, mode },
            cancellationToken);

    public Task<JsonElement> RetryLatestAsync(CancellationToken cancellationToken = default) =>
        SendCommandAsync("retry_latest", null, cancellationToken);

    public async Task<string?> GetLatestWavPathAsync(CancellationToken cancellationToken = default)
    {
        JsonElement result = await SendCommandAsync("get_latest_wav", null, cancellationToken);
        return result.TryGetProperty("path", out JsonElement path) && path.ValueKind == JsonValueKind.String
            ? path.GetString()
            : null;
    }

    public Task<JsonElement> AbandonCurrentAsync(CancellationToken cancellationToken = default) =>
        SendCommandAsync("abandon_current", null, cancellationToken);

    public Task<JsonElement> ClearHistoryAsync(CancellationToken cancellationToken = default) =>
        SendCommandAsync("clear_history", null, cancellationToken);

    public Task<JsonElement> ReloadProvidersAsync(CancellationToken cancellationToken = default) =>
        SendCommandAsync("reload_providers", null, cancellationToken);

    public async Task RestartAsync(CancellationToken cancellationToken = default)
    {
        SetConnectionState("正在重启 Python 后端…");
        await StopProcessAsync();
        await StartAsync(cancellationToken);
    }

    private async Task<JsonElement> SendCommandAsync(
        string command,
        object? arguments,
        CancellationToken cancellationToken)
    {
        Process? process = _process;
        if (process is null || process.HasExited)
        {
            throw new InvalidOperationException("Python 后端尚未运行。");
        }

        string requestId = Guid.NewGuid().ToString("N");
        TaskCompletionSource<JsonElement> completion = new(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!_pending.TryAdd(requestId, completion))
        {
            throw new InvalidOperationException("无法登记 GUI 请求。");
        }

        using CancellationTokenRegistration registration = cancellationToken.Register(() =>
        {
            if (_pending.TryRemove(requestId, out TaskCompletionSource<JsonElement>? pending))
            {
                pending.TrySetCanceled(cancellationToken);
            }
        });

        Dictionary<string, object?> payload = new()
        {
            ["protocol_version"] = 1,
            ["request_id"] = requestId,
            ["command"] = command,
        };
        if (arguments is not null)
        {
            foreach (System.Reflection.PropertyInfo property in arguments.GetType().GetProperties())
            {
                payload[property.Name] = property.GetValue(arguments);
            }
        }

        string line = CommandMarker + JsonSerializer.Serialize(payload);
        await _writeLock.WaitAsync(cancellationToken);
        try
        {
            await process.StandardInput.WriteLineAsync(line.AsMemory(), cancellationToken);
            await process.StandardInput.FlushAsync(cancellationToken);
        }
        catch
        {
            _pending.TryRemove(requestId, out _);
            throw;
        }
        finally
        {
            _writeLock.Release();
        }

        return await completion.Task;
    }

    private async Task ReadOutputAsync(StreamReader reader, CancellationToken cancellationToken)
    {
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                string? line = await reader.ReadLineAsync(cancellationToken);
                if (line is null)
                {
                    return;
                }
                RouteOutputLine(line);
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception exception)
        {
            AddLog($"读取 Python 输出失败：{exception.Message}", "#C42B1C");
        }
    }

    private async Task ReadErrorAsync(StreamReader reader, CancellationToken cancellationToken)
    {
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                string? line = await reader.ReadLineAsync(cancellationToken);
                if (line is null)
                {
                    return;
                }
                AddLog(AnsiEscape().Replace(line, string.Empty), "#C42B1C");
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    private void RouteOutputLine(string line)
    {
        if (!line.StartsWith(EventMarker, StringComparison.Ordinal))
        {
            AddLog(AnsiEscape().Replace(line, string.Empty));
            return;
        }

        try
        {
            using JsonDocument document = JsonDocument.Parse(line[EventMarker.Length..]);
            JsonElement root = document.RootElement;
            string eventName = root.TryGetProperty("event", out JsonElement eventElement)
                ? eventElement.GetString() ?? string.Empty
                : string.Empty;
            switch (eventName)
            {
                case "protocol_hello":
                    SetConnectionState("已连接");
                    break;
                case "service_snapshot":
                    if (root.TryGetProperty("snapshot", out JsonElement snapshotElement))
                    {
                        ServiceSnapshot? snapshot = snapshotElement.Deserialize<ServiceSnapshot>();
                        if (snapshot is not null)
                        {
                            LastSnapshot = snapshot;
                            SnapshotReceived?.Invoke(this, snapshot);
                            SetConnectionState("运行中");
                        }
                    }
                    break;
                case "command_result":
                    CompleteCommand(root);
                    break;
                case "status_overlay":
                    StatusOverlayEvent? overlayEvent = root.Deserialize<StatusOverlayEvent>();
                    if (overlayEvent is not null)
                    {
                        StatusOverlayReceived?.Invoke(this, overlayEvent);
                    }
                    break;
                case "daily_input_count":
                    if (root.TryGetProperty("count", out JsonElement countElement)
                        && countElement.TryGetInt32(out int count))
                    {
                        DailyInputCountChanged?.Invoke(this, Math.Max(0, count));
                    }
                    break;
                case "context_toggle":
                    ContextSettingChangedEvent? contextEvent = root.Deserialize<ContextSettingChangedEvent>();
                    if (contextEvent is not null)
                    {
                        ContextSettingChanged?.Invoke(this, contextEvent);
                    }
                    break;
                default:
                    if (root.TryGetProperty("text", out JsonElement textElement))
                    {
                        string? color = root.TryGetProperty("color", out JsonElement colorElement)
                            ? colorElement.GetString()
                            : null;
                        AddLog(textElement.GetString() ?? string.Empty, color);
                    }
                    break;
            }
        }
        catch (JsonException)
        {
            AddLog(line);
        }
    }

    private void CompleteCommand(JsonElement root)
    {
        if (!root.TryGetProperty("request_id", out JsonElement requestElement))
        {
            return;
        }
        string? requestId = requestElement.GetString();
        if (requestId is null || !_pending.TryRemove(requestId, out TaskCompletionSource<JsonElement>? completion))
        {
            return;
        }

        bool ok = root.TryGetProperty("ok", out JsonElement okElement) && okElement.GetBoolean();
        if (ok)
        {
            JsonElement result = root.TryGetProperty("result", out JsonElement resultElement)
                ? resultElement.Clone()
                : default;
            completion.TrySetResult(result);
            return;
        }

        string message = "Python 后端拒绝了请求。";
        if (root.TryGetProperty("error", out JsonElement errorElement)
            && errorElement.TryGetProperty("message", out JsonElement messageElement))
        {
            message = messageElement.GetString() ?? message;
        }
        completion.TrySetException(new InvalidOperationException(message));
    }

    private void AddLog(string text, string? color = null)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        LogEntry entry = new(DateTimeOffset.Now, text.TrimEnd(), color);
        _logs.Enqueue(entry);
        while (_logs.Count > MaxBufferedLogs)
        {
            _logs.TryDequeue(out _);
        }
        LogReceived?.Invoke(this, entry);
    }

    private void SetConnectionState(string state)
    {
        ConnectionState = state;
        ConnectionStateChanged?.Invoke(this, state);
    }

    private void FailPending(Exception exception)
    {
        foreach ((string requestId, TaskCompletionSource<JsonElement> completion) in _pending)
        {
            if (_pending.TryRemove(requestId, out _))
            {
                completion.TrySetException(exception);
            }
        }
    }

    private async Task StopProcessAsync()
    {
        _lifetime?.Cancel();
        Process? process = _process;
        _process = null;
        if (process is not null)
        {
            try
            {
                process.StandardInput.Close();
                if (!process.HasExited)
                {
                    process.Kill(entireProcessTree: true);
                    await process.WaitForExitAsync();
                }
            }
            catch (InvalidOperationException)
            {
            }
            finally
            {
                process.Dispose();
            }
        }
        _lifetime?.Dispose();
        _lifetime = null;
        FailPending(new ObjectDisposedException(nameof(PythonServiceClient)));
    }

    public async ValueTask DisposeAsync()
    {
        await StopProcessAsync();
        _writeLock.Dispose();
    }

    [GeneratedRegex("\\u001B\\[[0-?]*[ -/]*[@-~]")]
    private static partial Regex AnsiEscape();
}
