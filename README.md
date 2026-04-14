# claude-meeting-mcp

MCP Server cross-platform pour enregistrer des reunions et les transcrire automatiquement.

## Features

- Compatible avec toute app de visio (Google Meet, Teams, Zoom, Slack, Discord, etc.)
- Capture audio systeme + micro (stereo WAV, L=systeme, R=micro)
- Transcription avec Whisper en local ou via API remote
- Attribution automatique des locuteurs (canaux stereo L/R)
- Mode remote optionnel (API compatible OpenAI Whisper)
- Configuration globale TOML (modele, langue, speakers)
- Retention configurable des enregistrements (30 jours par defaut)
- Integration Claude via MCP (Claude Code, Claude Desktop, Cowork)

## Compatibilite

| | macOS Apple Silicon | macOS Intel | Windows | Linux |
|---|---|---|---|---|
| Capture systeme | Core Audio Taps | Core Audio Taps | WASAPI loopback | PipeWire/PulseAudio |
| Transcription | mlx-whisper | faster-whisper | faster-whisper | faster-whisper |

## Prerequis

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`brew install uv` / `pip install uv`)
- **macOS** : Xcode Command Line Tools (pour compiler audiocap)
- **Windows** : aucun prerequis supplementaire
- **Linux** : `libportaudio2` (`sudo apt install libportaudio2`)

## Installation

```bash
# Installer les dependances Python
uv sync

# macOS uniquement : compiler le binaire de capture audio
cd src/audiocap && swift build -c release && cd ../..

# Lancer le MCP server
uv run claude-meeting-mcp
```

## Configuration Claude Desktop / Claude Code

Ajouter dans la config MCP :

```json
{
  "mcpServers": {
    "claude-meeting-mcp": {
      "command": "uv",
      "args": ["--directory", "/path/to/claude-meeting-mcp", "run", "claude-meeting-mcp"]
    }
  }
}
```

## Configuration

Fichier de configuration : `~/.config/claude-meeting-mcp/config.toml`

```toml
[whisper]
model = "large-v3-turbo"   # tiny, base, small, medium, large-v3-turbo, large-v3
language = "fr"
mode = "local"              # "local" ou "remote"

[whisper.remote]
url = ""                    # URL API compatible OpenAI Whisper
api_key_env = "WHISPER_API_KEY"

[recording]
left_speaker = "Bruno"
right_speaker = "Delanoe"

[pv]
auto_generate = true
```

Ou via le tool MCP :
```
configure("whisper.model", "small")
configure("whisper.mode", "remote")
configure("whisper.remote.url", "https://api.groq.com/openai/v1/audio/transcriptions")
```

## Tools MCP disponibles

| Tool | Description |
|------|-------------|
| `check_status` | Statut du serveur (plateforme, backends, modele) |
| `record_start` | Demarrer l'enregistrement |
| `record_stop` | Arreter l'enregistrement |
| `transcribe` | Transcrire un fichier WAV |
| `record_and_transcribe` | Stop + transcription en un seul appel |
| `get_transcription` | Recuperer une transcription passee |
| `recordings_list` | Lister les enregistrements |
| `transcriptions_list` | Lister les transcriptions |
| `pvs_list` | Lister les PV de reunion |
| `configure` | Modifier la configuration |
| `cleanup` | Supprimer les enregistrements > 30 jours |

## Licence

Apache 2.0 - Delanoe Pirard / Aedelon
