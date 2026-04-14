# claude-meeting-mcp

## Projet
MCP Server pour enregistrer des réunions (Google Meet) et les transcrire automatiquement.
Développé par Delanoe pour le projet d'app comptable avec Bruno.

## Architecture

```
audiocap (Swift CLI)          → Capture audio micro + système via Core Audio Taps
    ↓ fichier .wav stéréo (L=système, R=micro)
claude_meeting_mcp (Python)   → MCP Server qui expose les tools à Claude
    ↓ utilise MLX-Whisper
transcription .json           → Segments avec timestamps et speaker attribution
```

## Stack technique
- Python 3.11+ avec uv (gestionnaire de paquets)
- MCP SDK Python (FastMCP) pour le serveur
- MLX-Whisper pour la transcription locale (Apple Silicon)
- Swift CLI pour la capture audio (Core Audio Taps, macOS 14.4+)
- macOS uniquement (Apple Silicon M4 Pro, 24 Go RAM)

## Conventions
- Langue du code : anglais (noms de variables, commentaires, docstrings)
- Langue des docs utilisateur et CLAUDE.md : français
- Format audio : WAV stéréo 44.1kHz 16-bit (canal L = système, canal R = micro)
- Format transcription : JSON (voir schema dans src/claude_meeting_mcp/schemas.py)
- Nommage recordings : YYYY-MM-DD_HHhMM_meeting.wav
- Nommage transcriptions : YYYY-MM-DD_HHhMM_meeting.json
- Rétention : 30 jours pour les fichiers audio, transcriptions conservées indéfiniment

## Commandes utiles
```bash
# Lancer le MCP server en dev
uv run claude-meeting-mcp

# Compiler le binaire Swift audiocap
cd src/audiocap && swift build -c release

# Lancer les tests
uv run pytest

# Transcrire un fichier manuellement
uv run transcribe recordings/2026-04-15_14h00_meeting.wav
```

## Structure du repo
```
src/
├── claude_meeting_mcp/     # MCP Server Python
│   ├── __init__.py
│   ├── server.py           # Point d'entrée FastMCP, définition des tools
│   ├── recorder.py         # Gestion de l'enregistrement (appel audiocap)
│   ├── transcriber.py      # Transcription MLX-Whisper + fusion canaux L/R
│   ├── storage.py          # Gestion fichiers, rétention 30 jours, listing
│   └── schemas.py          # Schémas JSON pour les transcriptions
├── audiocap/               # CLI Swift pour capture audio
│   ├── Package.swift
│   └── Sources/
│       └── AudioCap/
│           └── main.swift  # Core Audio Taps + aggregate device + écriture WAV
tests/
├── test_transcriber.py
├── test_storage.py
└── test_server.py
recordings/                 # Fichiers .wav (gitignored)
transcriptions/             # Fichiers .json
scripts/
└── cleanup.py              # Nettoyage des recordings > 30 jours
```

## Garde-fous
- Jamais d'envoi de données audio vers un service cloud
- Tout le traitement est local (MLX-Whisper sur Apple Silicon)
- Les recordings contiennent potentiellement des données sensibles (conversations avec Bruno)
- Ne pas commit les fichiers .wav ni les transcriptions dans git
