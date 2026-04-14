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
    assert config.whisper.model == "large-v3-turbo"
    assert config.whisper.language == "fr"
    assert config.whisper.mode == "local"
    assert config.recording.left_speaker == "Bruno"
    assert config.recording.right_speaker == "Delanoe"
    assert config.recording.sample_rate == 44100
    assert config.pv.auto_generate is True


def test_apply_toml_partial():
    config = Config()
    data = {"whisper": {"model": "small", "language": "en"}}
    _apply_toml_to_config(config, data)
    assert config.whisper.model == "small"
    assert config.whisper.language == "en"
    # Unchanged defaults
    assert config.whisper.mode == "local"
    assert config.recording.left_speaker == "Bruno"


def test_apply_toml_remote():
    config = Config()
    data = {
        "whisper": {
            "mode": "remote",
            "remote": {"url": "https://api.example.com/v1/audio/transcriptions"},
        }
    }
    _apply_toml_to_config(config, data)
    assert config.whisper.mode == "remote"
    assert config.whisper.remote.url == "https://api.example.com/v1/audio/transcriptions"


def test_apply_toml_recording():
    config = Config()
    data = {"recording": {"left_speaker": "Alice", "sample_rate": 16000}}
    _apply_toml_to_config(config, data)
    assert config.recording.left_speaker == "Alice"
    assert config.recording.sample_rate == 16000
    assert config.recording.right_speaker == "Delanoe"  # unchanged


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
    config.whisper.model = "nonexistent"
    errors = validate_config(config)
    assert len(errors) == 1
    assert "Invalid whisper model" in errors[0]


def test_validate_invalid_mode():
    config = Config()
    config.whisper.mode = "cloud"
    errors = validate_config(config)
    assert any("Invalid whisper mode" in e for e in errors)


def test_validate_remote_without_url():
    config = Config()
    config.whisper.mode = "remote"
    config.whisper.remote.url = ""
    errors = validate_config(config)
    assert any("remote.url" in e.lower() or "Remote mode" in e for e in errors)


def test_validate_unusual_sample_rate():
    config = Config()
    config.recording.sample_rate = 8000
    errors = validate_config(config)
    assert any("sample rate" in e.lower() for e in errors)


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
    config.whisper.model = "medium"
    config.whisper.mode = "remote"
    config.whisper.remote.url = "https://example.com/transcribe"
    config.recording.left_speaker = "Alice"
    config.pv.auto_generate = False

    save_config(config)

    loaded = load_config()
    assert loaded.whisper.model == "medium"
    assert loaded.whisper.mode == "remote"
    assert loaded.whisper.remote.url == "https://example.com/transcribe"
    assert loaded.recording.left_speaker == "Alice"
    assert loaded.pv.auto_generate is False


def test_load_config_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))
    config = load_config()
    # Should return defaults
    assert config.whisper.model == "large-v3-turbo"


def test_update_config_whisper_model(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))
    reload_config()

    updated = update_config("whisper.model", "small")
    assert updated.whisper.model == "small"

    # Verify persistence
    loaded = load_config()
    assert loaded.whisper.model == "small"


def test_update_config_remote_url(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))
    reload_config()

    updated = update_config("whisper.remote.url", "https://api.test.com/v1")
    assert updated.whisper.remote.url == "https://api.test.com/v1"


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


def test_update_config_pv_auto_generate(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_MEETING_CONFIG_DIR", str(tmp_path))
    reload_config()

    updated = update_config("pv.auto_generate", "false")
    assert updated.pv.auto_generate is False
