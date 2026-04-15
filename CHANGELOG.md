# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-04-15

### Added
- **Cross-platform support**: Windows (WASAPI loopback), Linux (PipeWire/PulseAudio)
- **Audio capture**: audiocap Swift CLI (Core Audio Taps) fully implemented for macOS
- **Dual transcription backend**: mlx-whisper (macOS ARM) + faster-whisper (Win/Linux/Intel)
- **Remote transcription**: any OpenAI-compatible API (Groq, Deepgram, self-hosted)
- **Speaker diarization**: pyannote-audio 3.1 with per-channel identification
- **Meeting minutes (PV)**: auto-generation via MCP Sampling with map-reduce for long meetings
- **Speaker identification by LLM**: Claude identifies who said what from conversation content
- **Audio processing chain**: RMS normalization + 4:1 compressor + limiter (stateful)
- **Microphone capture**: separate IOProc on macOS, sounddevice on Win/Linux
- **Mic resampling**: 44.1kHz → 48kHz software interpolation on macOS
- **16kHz resample**: before Whisper to avoid transcription artifacts
- **Anti-hallucination**: hallucination_silence_threshold, condition_on_previous_text=False
- **Parallel transcription**: ThreadPoolExecutor for faster-whisper/remote (not mlx)
- **Parallel PV generation**: asyncio.gather for map-reduce chunk summaries
- **Incremental WAV writing**: Win/Linux flush every 500ms (prevents total data loss)
- **Recording timeout**: 4h safety limit with auto-stop
- **Session onboarding**: capabilities injected into first tool result
- **Configuration wizard**: audio_configure() without params shows interactive menu
- **13 MCP tools** (audio_* prefix), 2 resources, 2 prompts
- **Multilingual**: 99 Whisper languages, Claude responds in user's language
- **Logging**: all critical modules (recorder, transcriber, diarize, pv_generator, _macos)
- **GitHub Actions CI**: multi-OS matrix (macOS, Windows, Ubuntu) x Python 3.11/3.13
- **Apache 2.0 LICENSE** file

### Changed
- Config section renamed: `[whisper]` → `[transcription]` (supports non-Whisper APIs)
- Tool prefix: `meeting_*` → `audio_*` (broader routing: YouTube, podcasts, not just meetings)
- Default language: `fr` → `en`
- Default sample rate: 44100 → 48000
- Storage: relative paths → platformdirs (XDG/macOS/Windows standard locations)
- `speakers` field: fixed left/right → dynamic per-meeting participants
- Instructions: compressed from ~500 to ~150 tokens per Anthropic best practices
- Tool descriptions: rewritten as "Use this when..." (action-oriented)

### Fixed
- Path traversal validation on all meeting_id inputs (tools + resources)
- RLock instead of Lock in recorder (prevents deadlock with timeout timer)
- Compressor envelope stateful across chunks (AudioProcessingState)
- stop_recording() resets state even on error (prevents permanent lockout)
- sf.read() with dtype=float32 + del data (50% less RAM)
- audio.astype(float32, copy=False) avoids unnecessary copies
- API keys never requested in conversation (security)
- Auto-cleanup of recordings >30 days on server startup

### Security
- Path traversal protection on meeting_id (tools + resources)
- Credentials via env vars only (TRANSCRIPTION_API_KEY, HF_TOKEN)
- Never ask API keys in Claude conversation
- .gitignore covers .env, recordings, transcriptions, PVs
- Thread-safe recording state (RLock)

## [0.1.0] - 2026-04-14

### Added
- Initial skeleton: FastMCP server with basic tools
- macOS-only: audiocap Swift CLI (skeleton)
- MLX-Whisper transcription
- Basic storage and schemas
