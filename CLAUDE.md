# claude-meeting-mcp

## Projet
MCP Server cross-platform pour enregistrer des reunions (Google Meet, Teams, Zoom, Slack, Discord, etc.) et les transcrire automatiquement avec identification des speakers.
Developpe par Delanoe pour le projet d'app comptable avec Bruno.

## Architecture

```
Capture audio (par OS)        → WAV stereo (L=systeme, R=micro)
  macOS : audiocap (Swift CLI, Core Audio Taps)
  Windows : PyAudioWPatch (WASAPI loopback) + sounddevice (mic)
  Linux : sounddevice (PipeWire/PulseAudio monitor + mic)
    ↓
Chaine audio                  → Normalisation RMS + Compresseur 4:1 + Limiter
    ↓
claude_meeting_mcp (Python)   → MCP Server qui expose les tools a Claude
    ↓
Transcription (par plateforme)
  macOS Apple Silicon : MLX-Whisper
  Windows/Linux/Intel : faster-whisper (CTranslate2)
  Optionnel : API remote (OpenAI-compatible)
    ↓
Diarization (optionnelle)     → pyannote-audio 3.1 (identification multi-speakers)
    ↓
transcription .json           → Segments avec timestamps et speaker attribution
    ↓
PV de reunion .md             → Genere automatiquement via MCP Sampling
                                (Claude identifie qui est qui par le contenu)
```

## Compatibilite

| | macOS Apple Silicon | macOS Intel | Windows | Linux |
|---|---|---|---|---|
| Audio systeme | Core Audio Taps | Core Audio Taps | WASAPI loopback | PipeWire monitor |
| Micro | Core Audio | Core Audio | sounddevice | sounddevice |
| Transcription | mlx-whisper | faster-whisper | faster-whisper | faster-whisper |
| Diarization | pyannote-audio | pyannote-audio | pyannote-audio | pyannote-audio |

## Stack technique
- Python 3.11+ avec uv (gestionnaire de paquets)
- MCP SDK Python (FastMCP) pour le serveur
- MLX-Whisper (macOS Apple Silicon) ou faster-whisper (Windows/Linux)
- Swift CLI audiocap pour la capture audio macOS (Core Audio Taps, macOS 14.4+)
- PyAudioWPatch pour la capture WASAPI loopback Windows
- sounddevice pour micro Windows/Linux + monitor PipeWire/PulseAudio
- pyannote-audio 3.1 pour la diarization multi-speakers (optionnel)
- platformdirs pour les chemins de donnees cross-platform
- httpx pour le mode de transcription remote
- Chaine audio : normalisation RMS, compresseur 4:1, limiter -0.5dB

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
sample_rate = 48000

[diarization]
enabled = false             # activer pour reunions multi-speakers
backend = "pyannote"        # none, pyannote, whisperx

[pv]
auto_generate = true
```

Les participants sont passes par reunion via les tools (pas dans la config globale) :
```
transcribe(file, remote_speakers="Bruno, Alice", local_speakers="Delanoe")
```

## Conventions
- Langue du code : anglais (noms de variables, commentaires, docstrings)
- Langue des docs utilisateur et CLAUDE.md : francais
- Format audio : WAV stereo 48kHz 16-bit (canal L = systeme, canal R = micro)
- Chaine audio : normalisation RMS → compresseur 4:1 → limiter -0.5dB
- Format transcription : JSON (voir schema dans src/claude_meeting_mcp/schemas.py)
- Nommage recordings : YYYY-MM-DD_HHhMM_meeting.wav
- Nommage transcriptions : YYYY-MM-DD_HHhMM_meeting.json
- Nommage PV : YYYY-MM-DD_HHhMM_meeting_pv.md
- Retention : 30 jours pour les fichiers audio, transcriptions conservees indefiniment

## Commandes utiles
```bash
# Installer les dependances
uv sync

# Avec diarization (optionnel, necessite HF_TOKEN)
uv sync --extra diarization

# Lancer le MCP server en dev
uv run claude-meeting-mcp

# Compiler le binaire Swift audiocap (macOS uniquement)
cd src/audiocap && swift build -c release

# Lancer les tests
uv run pytest -v

# Lint
uv run ruff check src/ tests/

# Transcrire un fichier manuellement
uv run transcribe path/to/meeting.wav "Bruno, Alice" "Delanoe"
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
│   ├── diarize.py          # Speaker diarization via pyannote-audio 3.1
│   ├── pv_generator.py     # Generation PV via MCP Sampling (map-reduce)
│   ├── storage.py          # Gestion fichiers cross-platform (platformdirs)
│   ├── schemas.py          # Schemas JSON pour les transcriptions
│   └── capture/            # Backends de capture audio par OS
│       ├── __init__.py     # Protocol AudioCapturer + factory
│       ├── audio_processing.py  # Chaine audio (normalise, compresse, limite)
│       ├── _macos.py       # Core Audio Taps via audiocap Swift
│       ├── _windows.py     # WASAPI loopback + sounddevice
│       └── _linux.py       # PipeWire/PulseAudio + sounddevice
├── audiocap/               # CLI Swift pour capture audio macOS
│   ├── Package.swift
│   └── Sources/AudioCap/
│       ├── main.swift          # Entry point CLI + SIGINT
│       ├── AudioTapManager.swift   # Core Audio Taps + aggregate device
│       ├── StereoRecorder.swift    # Dual IOProc (system+mic) + audio chain + WAV
│       └── RingBuffer.swift        # Ring buffer lock-free SPSC
tests/
├── test_config.py
├── test_transcriber.py
├── test_recorder.py
├── test_diarize.py
├── test_pv_generator.py
├── test_audio_processing.py
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
- La diarization pyannote necessite un token HuggingFace gratuit (1er telechargement)
- Les recordings contiennent potentiellement des donnees sensibles
- Ne pas commit les fichiers .wav ni les transcriptions dans git
- Permission TCC requise sur macOS pour la capture audio systeme
