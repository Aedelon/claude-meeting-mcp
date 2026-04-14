# claude-meeting-mcp

MCP Server pour enregistrer des réunions (Google Meet) et les transcrire automatiquement en local.

## Features

- Capture audio système + micro via Core Audio Taps (macOS 14.4+)
- Transcription locale avec MLX-Whisper (Apple Silicon)
- Attribution automatique des locuteurs (canaux stéréo L/R)
- Rétention configurable des enregistrements (30 jours par défaut)
- Intégration Claude via MCP (Cowork et Claude Code)

## Prérequis

- macOS 14.4+ (Apple Silicon)
- Python 3.11+
- Xcode Command Line Tools
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)

## Installation

```bash
# Installer les dépendances Python
uv sync

# Compiler le binaire de capture audio
cd src/audiocap && swift build -c release && cd ../..

# Lancer le MCP server
uv run claude-meeting-mcp
```

## Configuration Claude Code

Ajouter dans `~/.claude/claude_desktop_config.json` :

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

## Licence

MIT
