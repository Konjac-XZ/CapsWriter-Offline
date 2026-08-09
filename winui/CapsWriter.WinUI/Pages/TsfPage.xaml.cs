using System.Collections.ObjectModel;
using CapsWriter_WinUI.Models;
using CapsWriter_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CapsWriter_WinUI.Pages;

public sealed partial class TsfPage : Page
{
    private readonly PythonServiceClient _client;
    private bool _inspecting;

    public ObservableCollection<TsfLoadedDllState> Hosts { get; } = [];

    public TsfPage()
    {
        InitializeComponent();
        HostList.ItemsSource = Hosts;
        _client = ((App)Application.Current).ServiceClient;
    }

    private async void Page_Loaded(object sender, RoutedEventArgs e) =>
        await InspectAsync();

    private async void Refresh_Click(object sender, RoutedEventArgs e) =>
        await InspectAsync();

    private async Task InspectAsync()
    {
        if (_inspecting)
        {
            return;
        }
        _inspecting = true;
        InspectionProgress.IsActive = true;
        EmptyHostText.Visibility = Visibility.Collapsed;
        WarningPanel.Visibility = Visibility.Collapsed;
        try
        {
            TsfDllInspectionResult result = await _client.InspectTsfDllVersionsAsync();
            LatestVersionText.Text = result.Latest.Version;
            LatestCountText.Text = result.LatestCount.ToString();
            OldCountText.Text = result.OldCount.ToString();
            UnknownCountText.Text = result.UnknownCount.ToString();

            Hosts.Clear();
            foreach (TsfLoadedDllState host in result.Hosts
                .OrderBy(host => host.StatusOrder)
                .ThenBy(host => host.ProcessName, StringComparer.OrdinalIgnoreCase))
            {
                Hosts.Add(host);
            }
            HostCountText.Text = Hosts.Count.ToString();
            EmptyHostText.Text = "没有宿主加载 TIP DLL";
            EmptyHostText.Visibility = Hosts.Count == 0
                ? Visibility.Visible
                : Visibility.Collapsed;

            WarningList.ItemsSource = result.Warnings;
            WarningPanel.Visibility = result.Warnings.Count > 0
                ? Visibility.Visible
                : Visibility.Collapsed;
        }
        catch (Exception exception)
        {
            Hosts.Clear();
            LatestVersionText.Text = "检查失败";
            LatestCountText.Text = "—";
            OldCountText.Text = "—";
            UnknownCountText.Text = "—";
            HostCountText.Text = string.Empty;
            EmptyHostText.Text = $"检查失败：{exception.Message}";
            EmptyHostText.Visibility = Visibility.Visible;
        }
        finally
        {
            InspectionProgress.IsActive = false;
            _inspecting = false;
        }
    }
}
