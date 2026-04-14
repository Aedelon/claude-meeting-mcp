# claude-meeting-mcp

MCP Server cross-platform pour enregistrer des reunions et les transcrire automatiquement avec identification des speakers.

## Features

- Compatible avec toute app de visio (Google Meet, Teams, Zoom, Slack, Discord, etc.)
- Capture audio systeme + micro en stereo WAV (L=systeme, R=micro)
- Chaine audio : normalisation RMS + compresseur 4:1 + limiter
- Transcription avec Whisper en local ou via API remote
- Diarization multi-speakers via pyannote-audio 3.1 (optionnel)
- Identification des speakers par Claude (LLM) via le contenu de la conversation
- Generation automatique de PV (proces-verbal) via MCP Sampling
- Participants configures par reunion (pas de setup vocal prealable)
- Multilingue : 8 langues supportees (EN, FR, ES, IT, PT, RU, ZH, HE)
- Configuration globale TOML + override par tool
- Securise : path traversal protection, credentials via env vars, local par defaut
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
[transcription]
model = "large-v3-turbo"   # tiny, base, small, medium, large-v3-turbo, large-v3
language = "en"             # langue de la reunion
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

Ou via les tools MCP :
```
meeting_configure("transcription.model", "small")
meeting_configure("transcription.language", "fr")
meeting_configure("diarization.enabled", "true")
```

## Usage

### Reunion simple (1v1)
```
meeting_record_start()
# ... reunion ...
meeting_stop_and_transcribe(remote_speakers="Bruno", local_speakers="Delanoe")
```

### Reunion multi-participants
```
meeting_record_start()
# ... reunion avec plusieurs personnes ...
meeting_stop_and_transcribe(remote_speakers="Bruno, Alice, Charlie", local_speakers="Delanoe")
# → pyannote identifie les voix par canal
# → Claude attribue les vrais noms dans le PV
```

### Generation de PV
```
generate_meeting_pv(meeting_id="2026-04-14_14h00_meeting", participants="Bruno, Alice, Delanoe")
# → Claude genere le PV structure dans la langue de la transcription
```

## Tools MCP (13)

| Tool | Description |
|------|-------------|
| `meeting_status` | Statut serveur (plateforme, backends, modele, diarization) |
| `meeting_record_start` | Demarrer l'enregistrement stereo |
| `meeting_record_stop` | Arreter l'enregistrement |
| `meeting_transcribe` | Transcrire un fichier WAV avec speakers par reunion |
| `meeting_stop_and_transcribe` | Stop + transcription en un seul appel |
| `get_transcription` | Recuperer une transcription passee |
| `get_pv` | Recuperer un PV genere |
| `recordings_list` | Lister les enregistrements |
| `transcriptions_list` | Lister les transcriptions |
| `pvs_list` | Lister les PV de reunion |
| `generate_meeting_pv` | Generer un PV via MCP Sampling |
| `meeting_configure` | Modifier la configuration |
| `meeting_cleanup` | Supprimer les enregistrements > 30 jours |

## MCP Resources et Prompts

| Type | URI / Nom | Description |
|------|-----------|-------------|
| Resource | `transcription://{meeting_id}` | Transcription brute JSON |
| Resource | `pv://{meeting_id}` | PV genere en markdown |
| Prompt | `regenerate_pv` | Regenerer un PV avec instructions custom |
| Prompt | `extract_action_items` | Extraire les actions d'une reunion |

## Securite

- Local par defaut — aucune donnee envoyee au cloud
- Mode remote opt-in avec API choisie par l'utilisateur
- Credentials via env vars uniquement (`HF_TOKEN`, `TRANSCRIPTION_API_KEY`)
- Validation meeting_id contre path traversal
- Thread-safe (threading.Lock sur le recorder)
- .gitignore couvre .env, recordings, transcriptions, PV

## Licence

Apache 2.0 - Delanoe Pirard / Aedelon
