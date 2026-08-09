using Windows.Media.Core;
using Windows.Media.Playback;

namespace CapsWriter_WinUI.Services;

public sealed class LatestAudioPlayer : IDisposable
{
    private readonly MediaPlayer _player = new();

    public event EventHandler<bool>? PlaybackStateChanged;

    public bool IsPlaying => _player.PlaybackSession.PlaybackState is MediaPlaybackState.Playing
        or MediaPlaybackState.Buffering
        or MediaPlaybackState.Opening;

    public LatestAudioPlayer()
    {
        _player.AudioCategory = MediaPlayerAudioCategory.Media;
        _player.MediaEnded += (_, _) => PlaybackStateChanged?.Invoke(this, false);
        _player.MediaFailed += (_, args) =>
        {
            PlaybackStateChanged?.Invoke(this, false);
            PlaybackFailed?.Invoke(this, args.ErrorMessage);
        };
    }

    public event EventHandler<string>? PlaybackFailed;

    public async Task<bool> ToggleAsync(
        PythonServiceClient client,
        CancellationToken cancellationToken = default)
    {
        if (IsPlaying)
        {
            Stop();
            return false;
        }

        string? path = await client.GetLatestWavPathAsync(cancellationToken);
        if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
        {
            throw new FileNotFoundException("没有可播放的最近录音 WAV。");
        }

        _player.Source = MediaSource.CreateFromUri(new Uri(path));
        _player.Play();
        PlaybackStateChanged?.Invoke(this, true);
        return true;
    }

    public void Stop()
    {
        _player.Pause();
        _player.Source = null;
        PlaybackStateChanged?.Invoke(this, false);
    }

    public void Dispose()
    {
        _player.Dispose();
    }
}
