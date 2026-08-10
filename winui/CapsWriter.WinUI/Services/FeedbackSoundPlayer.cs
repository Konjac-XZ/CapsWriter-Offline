using System.Runtime.InteropServices;

namespace CapsWriter_WinUI.Services;

/// <summary>
/// Keeps short WAV cues pinned for the application's lifetime so hotkey feedback
/// does not pay process startup, file I/O, or decoder initialization costs.
/// </summary>
public sealed class FeedbackSoundPlayer : IDisposable
{
    private const uint SndAsync = 0x0001;
    private const uint SndNodefault = 0x0002;
    private const uint SndMemory = 0x0004;

    private readonly Dictionary<string, PinnedWave> _sounds = new(StringComparer.OrdinalIgnoreCase);
    private bool _disposed;

    public FeedbackSoundPlayer()
    {
        Load("start", "SpeechOn.wav");
        Load("stop", "SpeechOff.wav");
        Load("success", "bubble.wav");
    }

    public bool TryPlay(string cue)
    {
        if (_disposed || !_sounds.TryGetValue(cue, out PinnedWave? sound))
        {
            return false;
        }

        return PlaySound(
            sound.Pointer,
            nint.Zero,
            SndAsync | SndNodefault | SndMemory);
    }

    private void Load(string cue, string fileName)
    {
        string path = Path.Combine(
            AppContext.BaseDirectory,
            "Assets",
            "Feedback",
            fileName);
        try
        {
            if (File.Exists(path))
            {
                _sounds[cue] = new PinnedWave(File.ReadAllBytes(path));
            }
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        PlaySound(nint.Zero, nint.Zero, 0);
        foreach (PinnedWave sound in _sounds.Values)
        {
            sound.Dispose();
        }
        _sounds.Clear();
    }

    [DllImport("winmm.dll", EntryPoint = "PlaySoundW", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PlaySound(nint sound, nint module, uint flags);

    private sealed class PinnedWave : IDisposable
    {
        private GCHandle _handle;

        public PinnedWave(byte[] bytes)
        {
            _handle = GCHandle.Alloc(bytes, GCHandleType.Pinned);
        }

        public nint Pointer => _handle.AddrOfPinnedObject();

        public void Dispose()
        {
            if (_handle.IsAllocated)
            {
                _handle.Free();
            }
        }
    }
}
