# claude-meeting-mcp

## Projet
MCP Server cross-platform pour enregistrer des reunions (Google Meet, Teams, Zoom, Slack, Discord, etc.) et les transcrire automatiquement.
Developpe par Delanoe pour le projet d'app comptable avec Bruno.

## Architecture

```
Capture audio (par OS)        → WAV stereo (L=systeme, R=micro)
  macOS : audiocap (Swift CLI, Core Audio Taps)
  Windows : PyAudioWPatch (WASAPI loopback) + sounddevice (mic)
  Linux : sounddevice (PipeWire/PulseAudio monitor + mic)
    ↓
claude_meeting_mcp (Python)   → MCP Server qui expose les tools a Claude
    ↓
Transcription (par plateforme)
  macOS Apple Silicon : MLX-Whisper
  Windows/Linux/Intel : faster-whisper (CTranslate2)
  Optionnel : API remote (OpenAI-compatible)
    ↓
transcription .json           → Segments avec timestamps et speaker attribution
    ↓
PV de reunion .md             → Genere automatiquement via MCP Sampling
```

## Compatibilite

| | macOS Apple Silicon | macOS Intel | Windows | Linux |
|---|---|---|---|---|
| Audio systeme | Core Audio Taps | Core Audio Taps | WASAPI loopback | PipeWire monitor |
| Micro | Core Audio | Core Audio | sounddevice | sounddevice |
| Transcription | mlx-whisper | faster-whisper | faster-whisper | faster-whisper |

## Stack technique
- Python 3.11+ avec uv (gestionnaire de paquets)
- MCP SDK Python (FastMCP) pour le serveur
- MLX-Whisper (macOS Apple Silicon) ou faster-whisper (Windows/Linux)
- Swift CLI audiocap pour la capture audio macOS (Core Audio Taps, macOS 14.4+)
- PyAudioWPatch pour la capture WASAPI loopback Windows
- sounddevice pour micro Windows/Linux + monitor PipeWire/PulseAudio
- platformdirs pour les chemins de donnees cross-platform
- httpx pour le mode de transcription remote

## Configuration
Fichier : `~/.config/claude-meeting-mcp/config.toml` (Linux), `~/Library/Application Support/` (macOS), `%APPDATA%` (Windows)

```toml
[whisper]
model = "large-v3-turbo"   # tiny, base, small, medium, large-v3-turbo, large-v3
language = "fr"
mode = "local"              # "local" ou "remote"

[whisper.remote]
url = ""                    # API compatible OpenAI /v1/audio/transcriptions
api_key_env = "WHISPER_API_KEY"

[recording]
left_speaker = "Bruno"
right_speaker = "Delanoe"

[pv]
auto_generate = true
```

## Conventions
- Langue du code : anglais (noms de variables, commentaires, docstrings)
- Langue des docs utilisateur et CLAUDE.md : francais
- Format audio : WAV stereo 44.1kHz 16-bit (canal L = systeme, canal R = micro)
- Format transcription : JSON (voir schema dans src/claude_meeting_mcp/schemas.py)
- Nommage recordings : YYYY-MM-DD_HHhMM_meeting.wav
- Nommage transcriptions : YYYY-MM-DD_HHhMM_meeting.json
- Retention : 30 jours pour les fichiers audio, transcriptions conservees indefiniment

## Commandes utiles
```bash
# Installer les dependances
uv sync

# Lancer le MCP server en dev
uv run claude-meeting-mcp

# Compiler le binaire Swift audiocap (macOS uniquement)
cd src/audiocap && swift build -c release

# Lancer les tests
uv run pytest -v

# Lint
uv run ruff check src/ tests/

# Transcrire un fichier manuellement
uv run transcribe path/to/meeting.wav
```

## Structure du repo
```
src/
├── claude_meeting_mcp/
│   ├── __init__.py
│   ├── config.py           # Configuration globale TOML + defaults
│   ├── server.py           # Point d'entree FastMCP, definition des tools
│   ├── recorder.py         # Orchestration enregistrement (thin wrapper)
│   ├── transcriber.py      # Dual backend mlx/faster-whisper + remote
│   ├── storage.py          # Gestion fichiers cross-platform (platformdirs)
│   ├── schemas.py          # Schemas JSON pour les transcriptions
│   └── capture/            # Backends de capture audio par OS
│       ├── __init__.py     # Protocol AudioCapturer + factory
│       ├── _macos.py       # Core Audio Taps via audiocap Swift
│       ├── _windows.py     # WASAPI loopback + sounddevice
│       └── _linux.py       # PipeWire/PulseAudio + sounddevice
├── audiocap/               # CLI Swift pour capture audio macOS
│   ├── Package.swift
│   └── Sources/AudioCap/main.swift
tests/
├── test_config.py
├── test_transcriber.py
├── test_recorder.py
├── test_storage.py
└── test_server.py
scripts/
└── cleanup.py              # Nettoyage des recordings > 30 jours
.github/workflows/test.yml  # CI multi-OS (macOS, Windows, Ubuntu)
```

## Garde-fous
- Par defaut, jamais d'envoi de donnees audio vers un service cloud (mode "local")
- Le mode "remote" est opt-in et utilise une API choisie par l'utilisateur
- Tout le traitement local utilise Whisper sur le hardware de l'utilisateur
- Les recordings contiennent potentiellement des donnees sensibles
- Ne pas commit les fichiers .wav ni les transcriptions dans git
