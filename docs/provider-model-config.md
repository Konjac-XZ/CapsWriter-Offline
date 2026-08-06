# Transcription provider and model configuration

Transcription configuration has three separate identities:

- A **provider** owns credentials, endpoints, regions, and other shared connection
  settings.
- A **model** belongs to exactly one provider and declares its upstream model name
  and capabilities.
- The active selection is a `(provider_id, model_id)` pair stored in
  `config/transcription_state.yaml`.

Provider IDs are YAML filename stems. Model IDs are keys below `models`. The GUI
shows `model name · provider name`, so providers may expose models with identical
display names without making the selection ambiguous.

## Single provider and model

```yaml
schema_version: 2
name: Example OpenAI-compatible service
type: openai
description: Example only
hidden: false
settings:
  api_key: ${EXAMPLE_API_KEY}
  base_url: https://api.example.com
  language: zh
models:
  gpt-4o-mini-transcribe:
    name: GPT-4o Mini Transcribe
    upstream_model: gpt-4o-mini-transcribe
    modes:
      file_upload: {}
```

## OpenRouter with multiple models

```yaml
schema_version: 2
name: OpenRouter
type: openrouter
description: OpenRouter transcription models
settings:
  api_key: ${OPENROUTER_API_KEY}
  base_url: https://openrouter.ai
  dump_request_json: false
models:
  chirp-3:
    name: Chirp 3
    upstream_model: google/chirp-3
    incremental_output: true
    settings:
      prompt_provider_slug: google-vertex
    modes:
      file_upload: {}
  gpt-4o-transcribe:
    name: GPT-4o Transcribe
    upstream_model: openai/gpt-4o-transcribe
    incremental_output: true
    settings:
      prompt_provider_slug: openai
    modes:
      file_upload: {}
  gpt-transcribe:
    name: GPT Transcribe
    upstream_model: openai/gpt-transcribe
    incremental_output: true
    settings:
      prompt_provider_slug: openai
      languages: [zh-cn, en]
    modes:
      file_upload: {}
```

OpenRouter's top-level transcription `prompt` is ignored. The adapter therefore
places context below `provider.options[provider_slug]`: OpenAI transcription
models receive `prompt`, while Chirp 3 receives Google Speech V2's nested
`config.features.customPromptConfig`. GPT Transcribe additionally receives the
GUI user lexicon as `keywords` and its expected languages as `languages`.

All OpenRouter models above stream partial text from a completed file upload,
so they declare `incremental_output: true`. They do not declare `live_audio`:
incremental HTTP/SSE output is not a WebSocket Realtime transcription session.

Every provider supports `dump_request_json`, and the setting defaults to
`false`. Set it to `true` in that provider's top-level `settings` to write one
provider-bound request dump under the system temporary directory at
`CapsWriter-Offline/request-dumps/<provider-id>`. File-upload dumps include the
audio as Base64; live-audio dumps record the immutable session setup (streamed
audio chunks are not retained). Effective settings are included for debugging,
but API keys, secrets, tokens, passwords, and credentials are replaced with
`***`. The emitted `request_dump=...` log line reports the full path.

Another provider may also declare `name: GPT-4o Transcribe`; its composite model
reference remains different because its provider ID differs.

## File upload and live audio on one logical model

```yaml
schema_version: 2
name: Alibaba Model Studio
type: qwen-audio
description: Qwen Audio file and realtime transports
settings:
  api_key: ${DASHSCOPE_API_KEY}
  region: cn-beijing
models:
  qwen-audio-3-flash:
    name: Qwen Audio 3 Flash
    upstream_model: qwen-audio-3.0-asr-flash
    default_mode: file_upload
    modes:
      file_upload:
        settings:
          model: qwen-audio-3.0-asr-flash
      live_audio:
        settings:
          model: qwen-audio-3.0-asr-flash-streaming
```

`live_audio` means audio frames are sent while recording. It is independent of
incremental HTTP/SSE output after a complete file upload. A file model that emits
incremental response text can set `incremental_output: true` without declaring
`live_audio`.

## Migrating legacy YAML

Preview changes without writing:

```powershell
uv run python -m src.provider.migrate_config
```

Create timestamped `.bak` files and atomically write schema-v2 YAML:

```powershell
uv run python -m src.provider.migrate_config --write
```

Use `--config-dir PATH` for another provider directory. Legacy files remain
readable without migration. `settings.model` becomes the implicit `default`
model; `realtime` and `realtime_model` become live-audio mode configuration;
`stream` remains an output-delivery setting and does not enable live audio.
