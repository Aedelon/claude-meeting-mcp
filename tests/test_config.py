"""Tests for config module."""

from pathlib import Path

import pytest

from claude_meeting_mcp.config import (
    Config,
    _apply_toml_to_config,
    get_config_dir,
    get_faster_model_id,
    get_mlx_model_id,
    load_config,
    reload_config,
    save_config,
    update_config,
    validate_config,
)


def test_default_config():
    config = Config()
    assert config.transcription.model == "large-v3-turbo"
    assert config.transcription.language == "en"
    assert config.transcription.mode == "local"
    assert config.recording.sample_rate == 48000
    assert config.diarization.enabled is False
    assert config.diarization.backend == "none"
    assert config.pv.auto_generate is True


def test_apply_toml_partial():
    config = Config()
    data = {"transcription": {"model": "small", "language": "en"}}
    _apply_toml_to_config(config, data)
    assert config.transcription.model == "small"
    assert config.transcription.language == "en"
    assert config.transcription.mode == "local"


def test_apply_toml_remote():
    config = Config()
    data = {
        "transcription": {
            "mode": "remote",
            "remote": {"url": "https://api.example.com/v1/audio/transcriptions"},
        }
    }
    _apply_toml_to_config(config, data)
    assert config.transcription.mode == "remote"
    assert config.transcription.remote.url == "https://api.example.com/v1/audio/transcriptions"


def test_apply_toml_recording():
    config = Config()
    data = {"recording": {"sample_rate": 16000}}
    _apply_toml_to_config(config, data)
    assert config.recording.sample_rate == 16000


def test_apply_toml_diarization():
    config = Config()
    data = {"diarization": {"enabled": True, "backend": "whisperx"}}
    _apply_toml_to_config(config, data)
    assert config.diarization.enabled is True
    assert config.diarization.backend == "whisperx"


def test_apply_toml_pv():
    config = Config()
    data = {"pv": {"auto_generate": False}}
    _apply_toml_to_config(config, data)
    assert config.pv.auto_generate is False


def test_validate_config_valid():
    config = Config()
    errors = validate_config(config)
    assert errors == []


def test_validate_invalid_model():
    config = Config()
    config.transcription.model = "nonexistent"
    errors = validate_config(config)
    assert len(errors) == 1
    assert "Invalid transcription model" in errors[0]


def test_validate_invalid_mode():
    config = Config()
    config.transcription.mode = "cloud"
    errors = validate_config(config)
    assert any("Invalid transcription mode" in e for e in errors)


def test_validate_remote_without_url():
    config = Config()
    config.transcription.mode = "remote"
    config.transcription.remote.url = ""
    errors = validate_config(config)
    assert any("remote.url" in e.lower() or "Remote mode" in e for e in errors)


def test_validate_unusual_sample_rate():
    config = Config()
    config.recording.sample_rate = 8000
    errors = validate_config(config)
    assert any("sample rate" in e.lower() for e in errors)


def test_validate_invalid_diarization_backend():
    config = Config()
    config.diarization.backend = "invalid"
    errors = validate_config(config)
    assert any("diarization backend" in e.lower() for e in errors)


def test_mlx_model_mapping():
    assert get_mlx_model_id("tiny") == "mlx-community/whisper-tiny"
    assert get_mlx_model_id("large-v3") == "mlx-community/whisper-large-v3"
    assert get_mlx_model_id("unknown") == "mlx-community/whisper-large-v3-turbo"


def test_faster_model_mapping():
    assert get_faster_model_id("tiny") == "tiny"
    assert get_faster_model_id("large-v3") == "large-v3"
    assert get_faster_model_id("unknown") == "large-v3-turbo"


def test_config_dir_env_override(monkeypatch, tmp_path):
    custom_dir = str(tmp_path / "custom-config")
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", custom_dir)
    assert get_config_dir() == Path(custom_dir)


def test_save_and_load_config(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))

    config = Config()
    config.transcription.model = "medium"
    config.transcription.mode = "remote"
    config.transcription.remote.url = "https://example.com/transcribe"
    config.diarization.enabled = True
    config.diarization.backend = "whisperx"
    config.pv.auto_generate = False

    save_config(config)

    loaded = load_config()
    assert loaded.transcription.model == "medium"
    assert loaded.transcription.mode == "remote"
    assert loaded.transcription.remote.url == "https://example.com/transcribe"
    assert loaded.diarization.enabled is True
    assert loaded.diarization.backend == "whisperx"
    assert loaded.pv.auto_generate is False


def test_load_config_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))
    config = load_config()
    assert config.transcription.model == "large-v3-turbo"


def test_update_config_transcription_model(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))
    reload_config()

    updated = update_config("transcription.model", "small")
    assert updated.transcription.model == "small"

    loaded = load_config()
    assert loaded.transcription.model == "small"


def test_update_config_remote_url(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))
    reload_config()

    updated = update_config("transcription.remote.url", "https://api.test.com/v1")
    assert updated.transcription.remote.url == "https://api.test.com/v1"


def test_update_config_invalid_key(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))
    reload_config()

    with pytest.raises(ValueError, match="Unknown config key"):
        update_config("invalid.key", "value")


def test_update_config_sample_rate(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))
    reload_config()

    updated = update_config("recording.sample_rate", "48000")
    assert updated.recording.sample_rate == 48000


def test_update_config_diarization(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))
    reload_config()

    updated = update_config("diarization.backend", "whisperx")
    assert updated.diarization.backend == "whisperx"

    updated = update_config("diarization.enabled", "true")
    assert updated.diarization.enabled is True


def test_update_config_pv_auto_generate(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))
    reload_config()

    updated = update_config("pv.auto_generate", "false")
    assert updated.pv.auto_generate is False
