using Windows.Media.Core;
using Windows.Media.Playback;

namespace CapsWriter_WinUI.Services;

public sealed class LatestAudioPlayer : IDisposable
{
    private readonly MediaPlayer _player = new();
    private string? _playbackSnapshotPath;

    public event EventHandler<bool>? PlaybackStateChanged;

    public bool IsPlaying => _player.PlaybackSession.PlaybackState is MediaPlaybackState.Playing
        or MediaPlaybackState.Buffering
        or MediaPlaybackState.Opening;

    public LatestAudioPlayer()
    {
        _player.AudioCategory = MediaPlayerAudioCategory.Media;
        _player.MediaEnded += (_, _) => FinishPlayback();
        _player.MediaFailed += (_, args) =>
        {
            FinishPlayback();
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

        string snapshotPath = Path.Combine(
            Path.GetTempPath(), $"capswriter-playback-{Guid.NewGuid():N}.wav");
        _playbackSnapshotPath = snapshotPath;
        try
        {
            File.Copy(path, snapshotPath);
            _player.Source = MediaSource.CreateFromUri(new Uri(snapshotPath));
            _player.Play();
        }
        catch
        {
            _player.Source = null;
            DeletePlaybackSnapshot();
            throw;
        }
        PlaybackStateChanged?.Invoke(this, true);
        return true;
    }

    public void Stop()
    {
        _player.Pause();
        _player.Source = null;
        DeletePlaybackSnapshot();
        PlaybackStateChanged?.Invoke(this, false);
    }

    public void Dispose()
    {
        _player.Pause();
        _player.Source = null;
        DeletePlaybackSnapshot();
        _player.Dispose();
    }

    private void FinishPlayback()
    {
        _player.Source = null;
        DeletePlaybackSnapshot();
        PlaybackStateChanged?.Invoke(this, false);
    }

    private void DeletePlaybackSnapshot()
    {
        string? snapshotPath = _playbackSnapshotPath;
        _playbackSnapshotPath = null;
        if (snapshotPath is null)
        {
            return;
        }

        try
        {
            File.Delete(snapshotPath);
        }
        catch (IOException)
        {
            // MediaPlayer can release the file handle slightly after Source is
            // cleared. The unique temp file is harmless if cleanup races that
            // release; the next playback uses a different snapshot.
        }
        catch (UnauthorizedAccessException)
        {
            // See the IOException case above; do not turn normal playback
            // shutdown into a user-visible error.
        }
    }
}
