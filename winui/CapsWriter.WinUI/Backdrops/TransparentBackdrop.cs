using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using CompositionBrush = Windows.UI.Composition.CompositionBrush;
using Compositor = Windows.UI.Composition.Compositor;
using ICompositionSupportsSystemBackdrop = Microsoft.UI.Composition.ICompositionSupportsSystemBackdrop;

namespace CapsWriter_WinUI.Backdrops;

internal sealed class TransparentBackdrop : SystemBackdrop
{
    private Compositor? _compositor;
    private CompositionBrush? _brush;

    protected override void OnTargetConnected(
        ICompositionSupportsSystemBackdrop connectedTarget,
        XamlRoot xamlRoot)
    {
        _compositor = new Compositor();
        _brush = _compositor.CreateColorBrush(
            Windows.UI.Color.FromArgb(0, 0, 0, 0));
        connectedTarget.SystemBackdrop = _brush;
        base.OnTargetConnected(connectedTarget, xamlRoot);
    }

    protected override void OnTargetDisconnected(
        ICompositionSupportsSystemBackdrop disconnectedTarget)
    {
        CompositionBrush? brush = disconnectedTarget.SystemBackdrop;
        disconnectedTarget.SystemBackdrop = null;
        brush?.Dispose();
        _brush = null;
        _compositor?.Dispose();
        _compositor = null;
        base.OnTargetDisconnected(disconnectedTarget);
    }
}
