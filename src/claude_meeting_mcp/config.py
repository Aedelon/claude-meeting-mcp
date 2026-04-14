"""Global configuration system with TOML support."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import platformdirs

APP_NAME = "claude-meeting-mcp"

# Model name mappings per backend
MLX_MODEL_MAP: dict[str, str] = {
    "tiny": "mlx-community/whisper-tiny",
    "base": "mlx-community/whisper-base",
    "small": "mlx-community/whisper-small",
    "medium": "mlx-community/whisper-medium",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3",
}

FASTER_MODEL_MAP: dict[str, str] = {
    "tiny": "tiny",
    "base": "base",
    "small": "small",
    "medium": "medium",
    "large-v3-turbo": "large-v3-turbo",
    "large-v3": "large-v3",
}

VALID_MODELS = set(MLX_MODEL_MAP.keys())
VALID_MODES = {"local", "remote"}


@dataclass
class WhisperRemoteConfig:
    url: str = ""
    api_key_env: str = "WHISPER_API_KEY"


@dataclass
class WhisperConfig:
    model: str = "large-v3-turbo"
    language: str = "fr"
    mode: str = "local"
    remote: WhisperRemoteConfig = field(default_factory=WhisperRemoteConfig)


@dataclass
class RecordingConfig:
    left_speaker: str = "Bruno"
    right_speaker: str = "Delanoe"
    sample_rate: int = 44100


@dataclass
class PVConfig:
    auto_generate: bool = True


@dataclass
class Config:
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    pv: PVConfig = field(default_factory=PVConfig)


def get_config_dir() -> Path:
    """Platform-appropriate config directory."""
    env_override = os.environ.get("CLAUDE_MEETING_CONFIG_DIR")
    if env_override:
        return Path(env_override)
    return Path(platformdirs.user_config_dir(APP_NAME))


def get_config_path() -> Path:
    """Path to config.toml file."""
    return get_config_dir() / "config.toml"


def _apply_toml_to_config(config: Config, data: dict[str, Any]) -> None:
    """Apply parsed TOML data onto a Config instance."""
    if "whisper" in data:
        w = data["whisper"]
        if "model" in w:
            config.whisper.model = str(w["model"])
        if "language" in w:
            config.whisper.language = str(w["language"])
        if "mode" in w:
            config.whisper.mode = str(w["mode"])
        if "remote" in w:
            r = w["remote"]
            if "url" in r:
                config.whisper.remote.url = str(r["url"])
            if "api_key_env" in r:
                config.whisper.remote.api_key_env = str(r["api_key_env"])

    if "recording" in data:
        rec = data["recording"]
        if "left_speaker" in rec:
            config.recording.left_speaker = str(rec["left_speaker"])
        if "right_speaker" in rec:
            config.recording.right_speaker = str(rec["right_speaker"])
        if "sample_rate" in rec:
            config.recording.sample_rate = int(rec["sample_rate"])

    if "pv" in data:
        pv = data["pv"]
        if "auto_generate" in pv:
            config.pv.auto_generate = bool(pv["auto_generate"])


def validate_config(config: Config) -> list[str]:
    """Validate config values. Returns list of error messages (empty = valid)."""
    errors: list[str] = []
    if config.whisper.model not in VALID_MODELS:
        errors.append(
            f"Invalid whisper model '{config.whisper.model}'. "
            f"Valid: {', '.join(sorted(VALID_MODELS))}"
        )
    if config.whisper.mode not in VALID_MODES:
        errors.append(
            f"Invalid whisper mode '{config.whisper.mode}'. Valid: {', '.join(VALID_MODES)}"
        )
    if config.whisper.mode == "remote" and not config.whisper.remote.url:
        errors.append("Remote mode requires whisper.remote.url to be set")
    if config.recording.sample_rate not in (16000, 22050, 44100, 48000):
        errors.append(
            f"Unusual sample rate {config.recording.sample_rate}. "
            "Expected: 16000, 22050, 44100, or 48000"
        )
    return errors


def load_config() -> Config:
    """Load config from TOML file, falling back to defaults."""
    config = Config()
    config_path = get_config_path()

    if config_path.exists():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        _apply_toml_to_config(config, data)

    return config


def save_config(config: Config) -> Path:
    """Save current config to TOML file. Returns the path written."""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.toml"

    lines = [
        "[whisper]",
        f'model = "{config.whisper.model}"',
        f'language = "{config.whisper.language}"',
        f'mode = "{config.whisper.mode}"',
        "",
        "[whisper.remote]",
        f'url = "{config.whisper.remote.url}"',
        f'api_key_env = "{config.whisper.remote.api_key_env}"',
        "",
        "[recording]",
        f'left_speaker = "{config.recording.left_speaker}"',
        f'right_speaker = "{config.recording.right_speaker}"',
        f"sample_rate = {config.recording.sample_rate}",
        "",
        "[pv]",
        f"auto_generate = {'true' if config.pv.auto_generate else 'false'}",
        "",
    ]

    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def get_mlx_model_id(model_name: str) -> str:
    """Get MLX model HuggingFace repo ID from generic model name."""
    return MLX_MODEL_MAP.get(model_name, MLX_MODEL_MAP["large-v3-turbo"])


def get_faster_model_id(model_name: str) -> str:
    """Get faster-whisper model size from generic model name."""
    return FASTER_MODEL_MAP.get(model_name, FASTER_MODEL_MAP["large-v3-turbo"])


# Cached config singleton
_config: Config | None = None


def get_config() -> Config:
    """Get cached config (loads once)."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> Config:
    """Force reload config from disk."""
    global _config
    _config = load_config()
    return _config


def update_config(key: str, value: str) -> Config:
    """Update a single config key and save to disk.

    Args:
        key: Dot-separated key (e.g., 'whisper.model', 'recording.left_speaker')
        value: New value as string
    """
    config = get_config()

    parts = key.split(".")
    if len(parts) == 2:
        section, field_name = parts
        if section == "whisper" and field_name in ("model", "language", "mode"):
            setattr(config.whisper, field_name, value)
        elif section == "recording" and field_name in ("left_speaker", "right_speaker"):
            setattr(config.recording, field_name, value)
        elif section == "recording" and field_name == "sample_rate":
            config.recording.sample_rate = int(value)
        elif section == "pv" and field_name == "auto_generate":
            config.pv.auto_generate = value.lower() in ("true", "1", "yes")
        else:
            raise ValueError(f"Unknown config key: {key}")
    elif len(parts) == 3 and parts[0] == "whisper" and parts[1] == "remote":
        field_name = parts[2]
        if field_name in ("url", "api_key_env"):
            setattr(config.whisper.remote, field_name, value)
        else:
            raise ValueError(f"Unknown config key: {key}")
    else:
        raise ValueError(f"Unknown config key: {key}")

    save_config(config)
    return config
