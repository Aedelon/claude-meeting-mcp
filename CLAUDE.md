# claude-meeting-mcp

## Projet
MCP Server cross-platform pour enregistrer et transcrire n'importe quel audio : reunions (Google Meet, Teams, Zoom, Slack, Discord), videos YouTube, podcasts, musique, cours, interviews, etc.
Developpe par Delanoe pour le projet d'app comptable avec Bruno.

## Architecture

```
Capture audio (par OS)        → WAV stereo (L=systeme, R=micro)
  macOS : audiocap (Swift CLI, Core Audio Taps)
  Windows : PyAudioWPatch (WASAPI loopback) + sounddevice (mic)
  Linux : sounddevice (PipeWire/PulseAudio monitor + mic)
    ↓
Chaine audio                  → Normalisation RMS + Compresseur 4:1 + Limiter
  macOS : Swift (StereoRecorder.swift) — stateful envelope
  Windows/Linux : Python/scipy (audio_processing.py) — stateful AudioProcessingState
    ↓
Resample                      → 16kHz (ce que Whisper attend, evite les artefacts)
    ↓
claude_meeting_mcp (Python)   → MCP Server (13 tools, 2 resources, 2 prompts)
    ↓
Transcription (par plateforme)
  macOS Apple Silicon : MLX-Whisper (anti-hallucination: VAD + silence threshold)
  Windows/Linux/Intel : faster-whisper (CTranslate2, vad_filter=True)
  Optionnel : API remote (tout endpoint OpenAI-compatible, pas que Whisper)
    ↓
Diarization (optionnelle)     → pyannote-audio 3.1 (multi-speakers par canal)
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
| Parallelisme | sequentiel (GPU Metal) | parallele (CPU) | parallele (CPU/CUDA) | parallele (CPU/CUDA) |
| Diarization | pyannote-audio | pyannote-audio | pyannote-audio | pyannote-audio |

## Stack technique
- Python 3.11+ avec uv (gestionnaire de paquets)
- MCP SDK Python (FastMCP) pour le serveur
- MLX-Whisper (macOS Apple Silicon) ou faster-whisper (Windows/Linux)
- Swift CLI audiocap pour la capture audio macOS (Core Audio Taps, macOS 14.4+)
- PyAudioWPatch pour la capture WASAPI loopback Windows
- sounddevice pour micro Windows/Linux + monitor PipeWire/PulseAudio
- pyannote-audio 3.1 pour la diarization multi-speakers (optionnel)
- scipy pour la chaine audio vectorisee et le resample 16kHz
- platformdirs pour les chemins de donnees cross-platform
- httpx pour le mode de transcription remote

## Configuration
Fichier : `~/.config/claude-meeting-mcp/config.toml` (Linux), `~/Library/Application Support/` (macOS), `%APPDATA%` (Windows)

```toml
[transcription]
model = "large-v3-turbo"   # tiny, base, small, medium, large-v3-turbo, large-v3
language = "en"             # 99 langues supportees (codes ISO)
mode = "local"              # "local" ou "remote"

[transcription.remote]
url = ""                    # Any OpenAI-compatible /v1/audio/transcriptions API
api_key_env = "TRANSCRIPTION_API_KEY"

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
audio_transcribe(file, remote_speakers="Bruno, Alice", local_speakers="Delanoe")
audio_stop_and_transcribe(remote_speakers="Bruno", local_speakers="Delanoe")
audio_generate_pv(meeting_id="...", participants="Bruno, Alice, Delanoe")
```

## MCP Tools (13)

Tous prefixes `audio_` pour le routing (YouTube, podcasts, pas que reunions).
Descriptions ecrites en "Use this when..." (best practice Anthropic).

| Tool | Quand l'utiliser |
|------|-----------------|
| `audio_status` | Verifier le statut, les backends, la config |
| `audio_record_start` | Enregistrer l'audio du PC (reunions, YouTube, podcasts, musique...) |
| `audio_record_stop` | Arreter sans transcrire |
| `audio_transcribe` | Transcrire un fichier WAV existant |
| `audio_stop_and_transcribe` | Arreter ET transcrire (recommande) |
| `get_transcription` | Lire une transcription passee |
| `get_pv` | Lire un PV genere |
| `recordings_list` | Voir les enregistrements passes |
| `transcriptions_list` | Voir les transcriptions passees |
| `pvs_list` | Voir les PV generes |
| `audio_generate_pv` | Generer un PV structure apres transcription |
| `audio_configure` | Changer un parametre (langue, modele, diarization...) |
| `audio_cleanup` | Supprimer les enregistrements > 30 jours |

## MCP Resources & Prompts

| Type | URI / Nom | Description |
|------|-----------|-------------|
| Resource | `transcription://{meeting_id}` | Transcription brute JSON |
| Resource | `pv://{meeting_id}` | PV genere en markdown |
| Prompt | `regenerate_pv` | Regenerer un PV avec instructions custom |
| Prompt | `extract_action_items` | Extraire les actions d'une reunion |

## Multilinguisme
- Claude repond dans la langue de l'utilisateur (aucune limite)
- Whisper supporte 99 langues (configurable via transcription.language)
- PV genere dans la langue de la transcription
- Wizard de configuration pas a pas dans la langue de l'utilisateur

## Conventions
- Langue du code : anglais (noms de variables, commentaires, docstrings)
- Langue des docs utilisateur et CLAUDE.md : francais
- Format audio : WAV stereo 48kHz 16-bit (resample 16kHz avant Whisper)
- Chaine audio : normalisation RMS → compresseur 4:1 (stateful) → limiter -0.5dB
- Format transcription : JSON (voir schema dans src/claude_meeting_mcp/schemas.py)
- Nommage recordings : YYYY-MM-DD_HHhMM_meeting.wav
- Nommage transcriptions : YYYY-MM-DD_HHhMM_meeting.json
- Nommage PV : YYYY-MM-DD_HHhMM_meeting_pv.md
- Retention : 30 jours pour les fichiers audio (auto-cleanup au demarrage)
- Prefixe `audio_` sur tous les tools MCP (routing large : pas que reunions)

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
uv run transcribe path/to/audio.wav "Remote" "Local"
```

## Structure du repo
```
src/
├── claude_meeting_mcp/
│   ├── __init__.py
│   ├── config.py           # Configuration TOML (TranscriptionConfig, DiarizationConfig)
│   ├── server.py           # FastMCP server, 13 tools, 2 resources, 2 prompts
│   ├── recorder.py         # Orchestration enregistrement (RLock, timeout 4h)
│   ├── transcriber.py      # Dual backend + resample 16kHz + parallelisme
│   ├── diarize.py          # Speaker diarization via pyannote-audio 3.1
│   ├── pv_generator.py     # PV via MCP Sampling (map-reduce parallele)
│   ├── storage.py          # Gestion fichiers cross-platform (platformdirs)
│   ├── schemas.py          # Schemas JSON (Segment, Transcription)
│   └── capture/            # Backends de capture audio par OS
│       ├── __init__.py     # Protocol AudioCapturer + factory
│       ├── audio_processing.py  # Chaine audio stateful (AudioProcessingState)
│       ├── _macos.py       # Core Audio Taps via audiocap Swift + monitoring
│       ├── _windows.py     # WASAPI loopback + sounddevice + WAV incremental
│       └── _linux.py       # PipeWire/PulseAudio + sounddevice + WAV incremental
├── audiocap/               # CLI Swift pour capture audio macOS
│   ├── Package.swift
│   └── Sources/AudioCap/
│       ├── main.swift
│       ├── AudioTapManager.swift
│       ├── StereoRecorder.swift
│       └── RingBuffer.swift
tests/
├── test_config.py          # 22 tests config
├── test_transcriber.py     # 14 tests transcription
├── test_recorder.py        # 5 tests recorder
├── test_diarize.py         # 8 tests diarization
├── test_pv_generator.py    # 8 tests PV
├── test_audio_processing.py # 4 tests chaine audio
├── test_storage.py         # 8 tests storage
└── test_server.py          # 3 tests server
.github/workflows/test.yml  # CI multi-OS (macOS, Windows, Ubuntu)
```

## Securite
- Par defaut, jamais d'envoi de donnees audio vers un service cloud (mode "local")
- Le mode "remote" est opt-in avec l'API choisie par l'utilisateur
- Credentials uniquement via env vars (HF_TOKEN, TRANSCRIPTION_API_KEY)
- Jamais demander de cles API dans la conversation (securite MCP Sampling)
- Validation meeting_id contre path traversal sur tools ET resources
- recorder.py protege par RLock (reentrant, pas de deadlock avec timeout timer)
- Ecriture WAV incrementale (Win/Linux) — max 500ms de perte si crash
- Auto-cleanup des recordings > 30 jours au demarrage du serveur
- .gitignore couvre .env, recordings, transcriptions, PV
- Permission TCC requise sur macOS pour la capture audio systeme
