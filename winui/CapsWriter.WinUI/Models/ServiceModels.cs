using System.Text.Json.Serialization;
using Microsoft.UI.Xaml.Media;
using Windows.UI.ViewManagement;

namespace CapsWriter_WinUI.Models;

public sealed class ServiceSnapshot
{
    [JsonPropertyName("protocol_version")]
    public int ProtocolVersion { get; init; }

    [JsonPropertyName("service")]
    public ServiceState Service { get; init; } = new();

    [JsonPropertyName("active_model")]
    public ModelState? ActiveModel { get; init; }

    [JsonPropertyName("models")]
    public List<ModelState> Models { get; init; } = [];

    [JsonPropertyName("daily_input_count")]
    public int DailyInputCount { get; init; }

    [JsonPropertyName("session_constraint")]
    public string SessionConstraint { get; init; } = string.Empty;

    [JsonPropertyName("reflection")]
    public ReflectionState Reflection { get; init; } = new();

    [JsonPropertyName("tsf")]
    public TsfState Tsf { get; init; } = new();
}

public sealed class ServiceState
{
    [JsonPropertyName("process_id")]
    public int ProcessId { get; init; }

    [JsonPropertyName("recording")]
    public bool Recording { get; init; }

    [JsonPropertyName("transcribing")]
    public bool Transcribing { get; init; }

    [JsonPropertyName("active_task_id")]
    public string? ActiveTaskId { get; init; }
}

public sealed class ModelState
{
    [JsonPropertyName("provider_id")]
    public string ProviderId { get; init; } = string.Empty;

    [JsonPropertyName("model_id")]
    public string ModelId { get; init; } = string.Empty;

    [JsonPropertyName("provider_name")]
    public string ProviderName { get; init; } = string.Empty;

    [JsonPropertyName("model_name")]
    public string ModelName { get; init; } = string.Empty;

    [JsonPropertyName("label")]
    public string Label { get; init; } = string.Empty;

    [JsonPropertyName("input_mode")]
    public string InputMode { get; init; } = string.Empty;

    [JsonPropertyName("input_modes")]
    public List<string> InputModes { get; init; } = [];

    [JsonPropertyName("active")]
    public bool Active { get; init; }

    public string Key => $"{ProviderId}/{ModelId}";
}

public sealed class ReflectionState
{
    [JsonPropertyName("enabled")]
    public bool Enabled { get; init; }

    [JsonPropertyName("phase")]
    public string Phase { get; init; } = "starting";

    [JsonPropertyName("active_event_count")]
    public int ActiveEventCount { get; init; }

    [JsonPropertyName("last_started_at")]
    public double? LastStartedAt { get; init; }

    [JsonPropertyName("last_completed_at")]
    public double? LastCompletedAt { get; init; }

    [JsonPropertyName("last_outcome")]
    public string? LastOutcome { get; init; }

    [JsonPropertyName("last_error_type")]
    public string? LastErrorType { get; init; }

    [JsonPropertyName("storage")]
    public ReflectionStorageState? Storage { get; init; }
}

public sealed class ReflectionStorageState
{
    [JsonPropertyName("corrections")]
    public ReflectionCorrectionCounts Corrections { get; init; } = new();

    [JsonPropertyName("preferences")]
    public Dictionary<string, int> Preferences { get; init; } = [];

    [JsonPropertyName("latest_run")]
    public ReflectionRunState? LatestRun { get; init; }
}

public sealed class ReflectionCorrectionCounts
{
    [JsonPropertyName("total")]
    public int Total { get; init; }

    [JsonPropertyName("pending")]
    public int Pending { get; init; }

    [JsonPropertyName("due")]
    public int Due { get; init; }

    [JsonPropertyName("leased")]
    public int Leased { get; init; }
}

public sealed class ReflectionRunState
{
    [JsonPropertyName("started_at")]
    public double StartedAt { get; init; }

    [JsonPropertyName("completed_at")]
    public double? CompletedAt { get; init; }

    [JsonPropertyName("provider")]
    public string Provider { get; init; } = string.Empty;

    [JsonPropertyName("model")]
    public string Model { get; init; } = string.Empty;

    [JsonPropertyName("event_count")]
    public int EventCount { get; init; }

    [JsonPropertyName("outcome")]
    public string Outcome { get; init; } = string.Empty;

    [JsonPropertyName("error_type")]
    public string? ErrorType { get; init; }
}

public sealed class TsfState
{
    [JsonPropertyName("enabled")]
    public bool Enabled { get; init; }

    [JsonPropertyName("server_running")]
    public bool ServerRunning { get; init; }

    [JsonPropertyName("client_count")]
    public int ClientCount { get; init; }

    [JsonPropertyName("clients")]
    public List<TsfClientState> Clients { get; init; } = [];

    [JsonPropertyName("startup_error")]
    public string? StartupError { get; init; }

    [JsonPropertyName("composition")]
    public TsfCompositionState Composition { get; init; } = new();
}

public sealed class TsfClientState
{
    [JsonPropertyName("process_id")]
    public int ProcessId { get; init; }

    [JsonPropertyName("process_name")]
    public string ProcessName { get; init; } = "unknown";
}

public sealed class TsfCompositionState
{
    [JsonPropertyName("active")]
    public bool Active { get; init; }

    [JsonPropertyName("session_id")]
    public string? SessionId { get; init; }

    [JsonPropertyName("revision")]
    public int Revision { get; init; }

    [JsonPropertyName("captured")]
    public bool Captured { get; init; }

    [JsonPropertyName("style")]
    public string? Style { get; init; }

    [JsonPropertyName("host_process_id")]
    public int HostProcessId { get; init; }

    [JsonPropertyName("host_process_name")]
    public string? HostProcessName { get; init; }

    [JsonPropertyName("processor")]
    public string? Processor { get; init; }

    [JsonPropertyName("defer_final")]
    public bool DeferFinal { get; init; }
}

public sealed class ConfigurationState
{
    [JsonPropertyName("provider_id")]
    public string? ProviderId { get; init; }

    [JsonPropertyName("provider_name")]
    public string? ProviderName { get; init; }

    [JsonPropertyName("asr_prompt")]
    public string AsrPrompt { get; init; } = string.Empty;

    [JsonPropertyName("llm_prompt")]
    public string LlmPrompt { get; init; } = string.Empty;

    [JsonPropertyName("llm_enabled")]
    public bool LlmEnabled { get; init; }

    [JsonPropertyName("history_context_enabled")]
    public bool HistoryContextEnabled { get; init; }

    [JsonPropertyName("textbox_context_enabled")]
    public bool TextboxContextEnabled { get; init; }

    [JsonPropertyName("vision_context_enabled")]
    public bool VisionContextEnabled { get; init; }

    [JsonPropertyName("lexicon_text")]
    public string LexiconText { get; init; } = string.Empty;
}

public sealed class LogEntry
{
    private static readonly Windows.UI.Color DefaultForegroundColor = GetDefaultForegroundColor();

    public LogEntry(DateTimeOffset timestamp, string text, string? color = null)
    {
        Timestamp = timestamp;
        Text = text;
        Color = color;
        Foreground = ParseBrush(color);
    }

    public DateTimeOffset Timestamp { get; set; }
    public string Text { get; set; }
    public string? Color { get; set; }
    public Brush? Foreground { get; }
    public string DisplayTime => Timestamp.ToString("HH:mm:ss");

    private static Brush? ParseBrush(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return new SolidColorBrush(DefaultForegroundColor);
        }
        string hex = value.Trim().TrimStart('#');
        try
        {
            byte alpha = 255;
            int offset = 0;
            if (hex.Length == 8)
            {
                alpha = Convert.ToByte(hex[..2], 16);
                offset = 2;
            }
            else if (hex.Length != 6)
            {
                return new SolidColorBrush(DefaultForegroundColor);
            }
            byte red = Convert.ToByte(hex.Substring(offset, 2), 16);
            byte green = Convert.ToByte(hex.Substring(offset + 2, 2), 16);
            byte blue = Convert.ToByte(hex.Substring(offset + 4, 2), 16);
            return new SolidColorBrush(Windows.UI.Color.FromArgb(alpha, red, green, blue));
        }
        catch (FormatException)
        {
            return new SolidColorBrush(DefaultForegroundColor);
        }
    }

    private static Windows.UI.Color GetDefaultForegroundColor()
    {
        try
        {
            return new UISettings().GetColorValue(UIColorType.Foreground);
        }
        catch
        {
            return Windows.UI.Color.FromArgb(255, 32, 32, 32);
        }
    }
}

public sealed class ContextSettingChangedEvent
{
    [JsonPropertyName("target")]
    public string Target { get; init; } = string.Empty;

    [JsonPropertyName("enabled")]
    public bool Enabled { get; init; }
}

public sealed class StatusOverlayEvent
{
    [JsonPropertyName("action")]
    public string Action { get; init; } = string.Empty;

    [JsonPropertyName("state")]
    public string? State { get; init; }

    [JsonPropertyName("level")]
    public double? Level { get; init; }
}
