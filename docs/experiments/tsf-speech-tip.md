# TSF Speech TIP composition experiment

This experiment replaces simulated typing/paste with a real TSF composition only
while LLM polishing is enabled. ASR and LLM producers send **full text**, not
append-only deltas. The native text service owns the composition range until the
backend commits or cancels it.

## Why this shape

Microsoft describes a composition as temporary, still-changing input and uses
speech input as its concrete example. A text service obtains the focused document
manager with `ITfThreadMgr::GetFocus`, obtains its top edit context, and performs
all range mutations in read/write edit sessions. The experiment follows that
model:

1. `BEGIN(session, revision, full_text)` starts a composition at the current
   selection in the foreground process.
2. `REVISE(...)` replaces the complete `ITfComposition::GetRange()` text.
3. `COMMIT(...)` calls `ITfComposition::EndComposition` without changing the
   final text.
4. `CANCEL(...)` clears the range and then ends the composition.

The TIP is an in-process COM DLL. A pipe reader therefore never calls TSF from its
worker thread. It posts an owned frame to a message-only window created on the
TIP activation thread; that thread requests `TF_ES_ASYNC | TF_ES_READWRITE` and
applies the frame in `ITfEditSession::DoEditSession`.

Official references:

- [TSF architecture](https://learn.microsoft.com/en-us/windows/win32/tsf/architecture)
- [Compositions](https://learn.microsoft.com/en-us/windows/win32/tsf/compositions)
- [Edit contexts](https://learn.microsoft.com/en-us/windows/win32/tsf/edit-contexts)
- [`ITfContext::RequestEditSession`](https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itfcontext-requesteditsession)
- [`ITfContextComposition::StartComposition`](https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itfcontextcomposition-startcomposition)
- [`ITfRange::SetText`](https://learn.microsoft.com/en-us/windows/win32/api/msctf/nf-msctf-itfrange-settext)
- [Text service registration](https://learn.microsoft.com/en-us/windows/win32/tsf/text-service-registration)
- [Named-pipe security and access rights](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights)
- [Custom IME requirements](https://learn.microsoft.com/en-us/windows/apps/design/input/input-method-editor-requirements)

## IPC and fallback contract

The backend owns `\\.\pipe\CapsWriter.TsfSpeechTip.v1`. It creates multiple
duplex instances with an ACL granting access only to the current user and sets
`PIPE_REJECT_REMOTE_CLIENTS`. A TIP connects while its host is foreground, and
stays connected while it owns an active composition.

Frames have a fixed 40-byte little-endian header followed by UTF-16LE full text:

| Field | Size | Meaning |
| --- | ---: | --- |
| magic | 4 | `CWTP` |
| version | 2 | `1` |
| operation | 2 | begin/revise/commit/cancel or ACK bit |
| revision | 8 | strictly increasing per session |
| session | 16 | UUID in Windows/GUID byte order |
| text bytes | 4 | UTF-16LE byte count |
| status | 4 | ACK result or process ID for HELLO |

Pipe transport is event-driven in both directions. The broker creates overlapped
pipe instances and each client service thread blocks on the pending read, outgoing
queue event, or shutdown event. The TIP uses an overlapped read and waits on either
pipe completion or its shared wake event; `SetWinEventHook(EVENT_SYSTEM_FOREGROUND)`
wakes a background TIP as soon as its host becomes foreground. There are no fixed
10/25/250 ms transport or foreground polling loops. Disconnected clients retain a
bounded exponential reconnect delay after actual connection failures.

The activation-thread edit queue separately ensures that a TIP has at most one
outstanding TSF edit session and coalesces consecutive queued revisions without
crossing `BEGIN`, `COMMIT`, or `CANCEL`. All connected TIPs may receive `BEGIN`,
but only the instance loaded in the foreground process may accept it. The backend
suppresses the legacy keyboard/paste path only after an `APPLIED` ACK for that
session. A missing, rejected, or timed-out ACK keeps the entire task on the legacy
path, preventing text from disappearing merely because the experimental DLL is
absent.
Negative ACKs from background TIP instances never outrank a later positive ACK
from the foreground instance. On timeout, the broker also queues a higher-revision
`CANCEL`, so a delayed edit session cannot leave a composition behind after the
backend has chosen the legacy fallback.

Once a session is captured, the backend waits for an `APPLIED` ACK for every
`REVISE`. Finalization is an ordered barrier: the final full-text `REVISE(N)` must
be confirmed before `COMMIT(N+1)` is sent, and ownership is released only after
the commit is confirmed. A failed or timed-out commit keeps ownership available
for recovery. The pipeline may use the legacy paste fallback only after a
subsequent `CANCEL` is also confirmed; otherwise it suppresses fallback output to
avoid duplicating a composition that may have been applied after the timeout.

The request header's status field carries a backward-compatible visual state.
The TIP applies `GUID_PROP_ATTRIBUTE` over the complete composition range using
two display attributes: transcription revisions request `TF_LS_DASH`, while LLM
polishing revisions request `TF_LS_SOLID`. The property is cleared before commit
or cancellation. Older already-loaded DLLs ignore this request metadata, and
host applications that do not render TSF display attributes still retain the
same composition behavior without the requested underline.

## Build

From `native/tsf_speech_tip` with Visual Studio 2022 and a Windows SDK installed:

```text
cmake --preset vs2022-x64
cmake --build --preset x64-debug
cmake --preset vs2022-x86
cmake --build --preset x86-debug
```

Both architectures matter because TSF loads an in-process DLL matching the target
application's architecture.

## Self-signing and local installation

The checked-in signing files create a project-specific self-signed certificate
and install only its public half as a machine trust anchor. This is suitable for
our controlled machines, but it is not a substitute for a publicly trusted code-
signing certificate and must not be used for public distribution.

The certificate has these deliberate properties:

- RSA 3072-bit key and SHA-256 certificate/signature digest;
- Code Signing EKU only, with `CA=false`;
- ten-year validity;
- exportable private key stored in `CurrentUser\My`;
- public certificate installed in `LocalMachine\Root` and
  `LocalMachine\TrustedPublisher` so both 32-bit and 64-bit host processes on
  this controlled machine trust the signature.

From an elevated command prompt, create or reuse the certificate, then build,
sign, deploy to a stable ignored directory, and register both architectures:

```text
cd native\tsf_speech_tip
signing\create-certificate.cmd
cmake --preset vs2022-x64
cmake --build --preset x64-release
cmake --preset vs2022-x86
cmake --build --preset x86-release
signing\install-signed.cmd
```

After the certificate and machine trust have been created once, use the guarded
registration workflow for subsequent source updates:

```text
sudo native\tsf_speech_tip\register-tip.cmd
```

The workflow configures and builds both Release architectures, runs both CTest
suites, copies the outputs into a new unique directory under
`installed\versions`, signs those immutable copies, registers their absolute
paths, and verifies the x64 and x86 HKCU COM views plus the TSF language profile.
It never overwrites a loaded DLL: existing processes continue using their old
mapping, while processes started afterward load the newly registered version.
If signing, registration, or verification fails, the previous registry targets
are restored. It never creates or trusts a certificate implicitly.

Useful read-only and maintenance commands are:

```text
uv run python native\tsf_speech_tip\manage_registration.py status
uv run python native\tsf_speech_tip\manage_registration.py install --dry-run
uv run python native\tsf_speech_tip\manage_registration.py uninstall --dry-run
sudo uv run python native\tsf_speech_tip\manage_registration.py uninstall
```

`install --skip-build` reuses the existing x64/x86 Release outputs but still
signs, installs, registers, and verifies them. `uninstall` removes both
registrations without deleting versioned DLLs or the certificate. Running hosts
are reported for visibility but do not block either operation; they retain the
DLL they already loaded until they exit.
Mutating install and uninstall commands require elevation because TSF category
registration is machine-scoped; the workflow checks this before changing any
registration state.

`install-signed.cmd` remains as a compatibility entry point and delegates to the
same side-by-side workflow with `--skip-build`. The certificate, versioned DLLs,
`.cer`, and any `.pfx`/`.p12` backup are ignored by Git. Old version directories
are intentionally retained because Windows may still have those DLLs mapped;
they can be removed later after confirming that no process uses them.

To sign arbitrary build outputs without installing them:

```text
signing\sign.cmd <x64-or-x86-dll> [more-dlls...]
```

The scripts locate SignTool from an installed Windows SDK and use SHA-256. They
do not use an online timestamp service; therefore the DLL must be rebuilt and
re-signed (or the local certificate rotated) before certificate expiry.

### Reinstalling Windows or migrating to another controlled machine

The public `.cer` is not enough to sign future builds. Before reinstalling,
export the certificate **with its private key** from `certmgr.msc`:

1. Open `certmgr.msc` as the user that created the certificate.
2. Under **Personal > Certificates**, select
   **CapsWriter Offline Local Code Signing**.
3. Choose **All Tasks > Export**, include the private key, select PFX, use
   AES-256/SHA-256 when offered, and protect it with a strong unique password.
4. Store the PFX and its password outside the repository in two appropriately
   protected backup locations. Never commit or casually copy the PFX.

After reinstalling Windows or on a replacement machine:

1. Install Visual Studio 2022 C++ tools, a Windows SDK, CMake, Git, and the
   repository dependencies.
2. Import the backed-up PFX into the destination user's **Personal** store and
   mark the private key exportable only if future migration is required.
3. Run `create-certificate.cmd` elevated. It reuses the imported certificate,
   exports a public `.cer` if needed, and installs the public certificate into
   the two local-machine trust stores.
4. Rebuild both Release architectures and run `install-signed.cmd`.
5. Verify the two registry views and signatures as described below, then restart
   CapsWriter and test composition in both a 64-bit and a 32-bit host if 32-bit
   coverage matters.

If the PFX is lost, run `create-certificate.cmd` to mint a new identity, rebuild
and re-sign both DLLs, and remove the obsolete certificate from Personal,
Trusted Root Certification Authorities, and Trusted Publishers. Old binaries
signed with the lost identity will no longer be trusted after removal.

## Registration and controlled manual verification

Registration changes the current user's installed TSF profiles and causes the DLL
to be loaded inside newly started applications. Prefer the automated versioned
workflow. For manual x64 registration, run the 64-bit `regsvr32`; for x86
registration, use the copy under `SysWOW64`, and never overwrite a DLL that may
already be mapped by a running process:

```text
C:\Windows\System32\regsvr32.exe <absolute-x64-dll-path>
C:\Windows\SysWOW64\regsvr32.exe <absolute-x86-dll-path>
```

Then enable `client.tsf_speech_tip_enabled = true`, restart the CapsWriter client,
and verify in at least Notepad plus one Chromium/Electron editor:

1. With polishing enabled, realtime ASR text is visibly uncommitted.
2. LLM streaming replaces the entire composition without duplicate suffixes.
3. Final post-processing text is present after the composition commits.
4. Abandoning a task removes the composition.
5. With the TIP unavailable, the old keyboard/paste path still produces text.

Rollback, using the same binary paths and architecture-specific `regsvr32`:

```text
C:\Windows\System32\regsvr32.exe /u <absolute-x64-dll-path>
C:\Windows\SysWOW64\regsvr32.exe /u <absolute-x86-dll-path>
```

To remove the local trust identity as well, first unregister both DLLs and close
all hosts. Then delete the matching **CapsWriter Offline Local Code Signing**
certificate from `CurrentUser\My`, `LocalMachine\Root`, and
`LocalMachine\TrustedPublisher`. Removing trust is intentionally not automated:
certificate deletion affects every binary signed with that identity.

## Known experimental limits

- The local deployment is Authenticode-signed, but its self-signed identity is
  trusted only on machines where its public certificate was explicitly installed.
  Registration and signature verification still do not prove that every Windows
  app will load the TIP.
- The TIP provides dashed transcription and solid polishing display attributes,
  but rendering remains host-controlled. Applications can ignore or approximate
  the requested underline style.
- App-container and elevated-process coverage is unverified. Microsoft notes that
  an IME runs under the containing app's restrictions. The local named pipe is
  deliberately same-user only; integrity-level and app-container behavior needs
  real testing.
- Foreground ownership currently compares the foreground window PID with the DLL
  host PID. Multi-process applications can require a stronger focus signal.
- ACK proves that `DoEditSession` applied a frame. It does not yet expose a durable
  diagnostic/event stream to the GUI when a composition is later terminated by
  the host application.
