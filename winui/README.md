# CapsWriter WinUI 3 shell

This project is the native Windows 11 shell for the existing CapsWriter Python
runtime. It does not embed a browser or start a WebView. The WinUI process owns
the Python child process and communicates over redirected standard input and
output, so no local HTTP port or long-lived authentication token is required.

## Product shape

- The primary window is a compact monitor placed at the bottom-right of the
  primary work area. The log is the dominant surface.
- Closing the primary window hides it to the notification area. The tray menu
  can show the window, reload provider configuration, restart the Python
  backend, or quit the application.
- A separate always-on-top, no-activate window presents listening,
  transcribing, and polishing state. It includes elapsed time, the microphone
  level, and an abandon button.
- Prompt/configuration, reflection-loop status, TSF-TIP status, and diagnostics
  remain available through compact navigation instead of consuming monitor
  space.

The shell follows Microsoft's recommended WinUI 3 structure: a Mica backdrop,
a custom `TitleBar` above `NavigationView`, transparent page roots, and app
identity shown only in the title bar.

## Build and run

The project currently targets .NET 9 and Windows 11 build 22000 or later.

```text
dotnet restore winui/CapsWriter.WinUI/CapsWriter.WinUI.csproj --configfile winui/NuGet.Config
dotnet build winui/CapsWriter.WinUI/CapsWriter.WinUI.csproj
dotnet run --project winui/CapsWriter.WinUI/CapsWriter.WinUI.csproj
```

The normal launch expects `.venv/Scripts/python.exe` and `core_client.py` under
the same repository root. Set `CAPSWRITER_ROOT` when the executable cannot
discover the repository by walking upward from its working and application
directories.

For shell-only development, use the packaged debug runner's application
argument so no audio process is started:

```text
dotnet run --project winui/CapsWriter.WinUI/CapsWriter.WinUI.csproj -- --args --no-backend
```

`CAPSWRITER_WINUI_NO_BACKEND=1` is also accepted when the launch mechanism
preserves environment variables. AUMID activation does not always do so, which
is why the command-line switch is preferred.

## Deploy to this PC

After each accepted WinUI change, deploy the Release layout instead of leaving
the result only under `bin`:

```text
winui\deploy-local.cmd
```

Pass `-Launch` to start the newly registered build after deployment:

```text
winui\deploy-local.cmd -Launch
```

The script only stops `CapsWriter.WinUI.exe` processes whose executable path is
inside this checkout. It then restores, builds Release, registers the loose
AppX layout for the current user, and verifies both the package location and
the `CapsWriter Native` Start menu entry. The registration remains valid after
the script exits and points directly at the latest Release AppX layout. With
`-Launch`, it also verifies that the Start-menu-activated process survives and
that Windows did not record a launch error. The executable, window, tray, and
package image assets all use the repository's canonical
`assets/client-icon.ico` artwork.

## IPC contract

Protocol version 1 uses one JSON object per line:

- Python to WinUI: `CW_GUI:{...}`
- WinUI to Python: `CW_COMMAND:{...}`

Every command includes `protocol_version`, `request_id`, and `command`.
`command_result` returns the same request ID. Periodic `service_snapshot`
events contain operational metadata but deliberately exclude API keys,
reflection evidence text, current TSF text, and audio payloads.

The current command surface includes snapshots, the per-task temporary
instruction, model/mode selection, ASR and LLM prompts, context switches,
lexicon editing, recent-history clearing, provider reload, recent-recording
retry/playback lookup, abandon-current-task, and learned-preference listing,
editing, deletion, and clearing.

## Legacy GUI parity

| Legacy capability | Native shell status |
| --- | --- |
| Large live log and clear action | Connected; virtualized and bounded to 500 entries |
| Temporary task instruction | Connected; 400 ms debounce and backend acknowledgement |
| Model selection and live/file mode | Connected; applies to the next recording |
| ASR/LLM prompt editing | Connected through the Python configuration API |
| History/textbox/active-control-state/vision context switches | Connected; vision worker starts/stops with the switch |
| User lexicon | Connected as one entry per line; YAML remains the persisted format |
| Clear recent polish history | Connected |
| Retry, play, and abandon | Connected |
| Listening/transcribing/polishing overlay | Implemented as a native secondary window |
| Tray show/reload/restart/quit | Implemented with `Shell_NotifyIcon` |
| Reflection-loop status | Connected with content-free queue/run metadata |
| TSF-TIP status | Connected with broker, client, and composition metadata |

## Validation boundaries

An ordinary build verifies XAML compilation and Win32/WinUI interop signatures.
Python tests verify command correlation, protocol-version rejection,
content-free snapshots, lexicon persistence, and configuration updates.

The following still require an interactive Windows session and real devices:

- tray icon mouse/keyboard behavior after Explorer restarts;
- multi-monitor overlay placement at mixed DPI values;
- microphone level animation and state transitions during a paid-provider
  request;
- playback against an actual `.tmp/retry_audio/latest.wav`;
- packaged installation/update behavior outside the repository checkout.

The old Qt entry point remains available during migration. Do not run both GUI
shells against the same checkout at the same time because both attempt to own a
`core_client.py` process.

## Primary platform references

- <https://learn.microsoft.com/en-us/windows/apps/develop/ui/windows-app-sdk-app-structure>
- <https://learn.microsoft.com/en-us/windows/apps/develop/ui/controls/navigationview>
- <https://learn.microsoft.com/en-us/windows/apps/develop/title-bar?tabs=winui3>
- <https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw>
- <https://learn.microsoft.com/en-us/windows/apps/develop/ui/manage-app-windows>
