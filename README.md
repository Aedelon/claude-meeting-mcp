# claude-meeting-mcp

MCP Server cross-platform pour enregistrer des reunions et les transcrire automatiquement avec identification des speakers.

## Features

- Compatible avec toute app de visio (Google Meet, Teams, Zoom, Slack, Discord, etc.)
- Capture audio systeme + micro en stereo WAV (L=systeme, R=micro)
- Chaine audio : normalisation RMS + compresseur 4:1 + limiter
- Transcription avec Whisper en local ou via API remote
- Diarization multi-speakers via pyannote-audio 3.1 (optionnel)
- Identification des speakers par Claude (LLM) lors de la generation du PV
- Generation automatique de PV (proces-verbal) via MCP Sampling
- Participants configures par reunion (pas de setup vocal prealable)
- Configuration globale TOML + override par tool
- Retention configurable des enregistrements (30 jours par defaut)
- Integration Claude via MCP (Claude Code, Claude Desktop, Cowork)

## Compatibilite

| | macOS Apple Silicon | macOS Intel | Windows | Linux |
|---|---|---|---|---|
| Capture systeme | Core Audio Taps | Core Audio Taps | WASAPI loopback | PipeWire/PulseAudio |
| Transcription | mlx-whisper | faster-whisper | faster-whisper | faster-whisper |
| Diarization | pyannote-audio | pyannote-audio | pyannote-audio | pyannote-audio |

## Prerequis

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`brew install uv` / `pip install uv`)
- **macOS** : Xcode Command Line Tools + permission audio systeme (Reglages > Confidentialite > Enregistrement audio systeme)
- **Windows** : aucun prerequis supplementaire
- **Linux** : `libportaudio2` (`sudo apt install libportaudio2`)
- **Diarization** (optionnel) : token HuggingFace gratuit (`HF_TOKEN`)

## Installation

```bash
# Installer les dependances Python
uv sync

# Avec diarization multi-speakers (optionnel)
uv sync --extra diarization

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
sample_rate = 48000

[diarization]
enabled = false             # activer pour reunions multi-speakers
backend = "pyannote"        # none, pyannote, whisperx

[pv]
auto_generate = true
```

Ou via les tools MCP :
```
configure("whisper.model", "small")
configure("diarization.enabled", "true")
configure("diarization.backend", "pyannote")
```

## Usage

### Reunion simple (1v1)
```
record_start()
# ... reunion ...
record_and_transcribe(remote_speakers="Bruno", local_speakers="Delanoe")
```

### Reunion multi-participants
```
record_start()
# ... reunion avec plusieurs personnes ...
record_and_transcribe(remote_speakers="Bruno, Alice, Charlie", local_speakers="Delanoe")
# → pyannote identifie les voix par canal
# → Claude attribue les vrais noms dans le PV
```

### Generation de PV
```
generate_meeting_pv(meeting_id="2026-04-14_14h00_meeting", participants="Bruno, Alice, Delanoe")
# → Claude genere le PV structure avec les vrais noms
```

## Tools MCP disponibles

| Tool | Description |
|------|-------------|
| `check_status` | Statut du serveur (plateforme, backends, modele) |
| `record_start` | Demarrer l'enregistrement |
| `record_stop` | Arreter l'enregistrement |
| `transcribe` | Transcrire un fichier WAV avec speakers par reunion |
| `record_and_transcribe` | Stop + transcription en un seul appel |
| `get_transcription` | Recuperer une transcription passee |
| `generate_meeting_pv` | Generer un PV via MCP Sampling |
| `get_pv` | Recuperer un PV genere |
| `recordings_list` | Lister les enregistrements |
| `transcriptions_list` | Lister les transcriptions |
| `pvs_list` | Lister les PV de reunion |
| `configure` | Modifier la configuration |
| `cleanup` | Supprimer les enregistrements > 30 jours |

## MCP Resources et Prompts

| Type | URI / Nom | Description |
|------|-----------|-------------|
| Resource | `transcription://{meeting_id}` | Transcription brute |
| Resource | `pv://{meeting_id}` | PV genere |
| Prompt | `regenerate_pv` | Regenerer un PV avec instructions custom |
| Prompt | `extract_action_items` | Extraire les actions d'une reunion |

## Licence

Apache 2.0 - Delanoe Pirard / Aedelon
