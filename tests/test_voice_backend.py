from __future__ import annotations

import json
from pathlib import Path

from agent_slack.voice import LocalVoiceBackend


def test_whisper_cpp_adapter_converts_browser_audio_and_reads_json(monkeypatch, tmp_path):
    backend = LocalVoiceBackend()
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"model")
    backend.whisper_model = str(model)
    calls = []

    monkeypatch.setattr(backend, "_command_available", staticmethod(lambda _command: True))

    def run(command, **_kwargs):
        calls.append(command)
        if "-of" in command:
            output = Path(command[command.index("-of") + 1]).with_suffix(".json")
            output.write_text(json.dumps({"transcription": [{"text": "find the report"}]}), encoding="utf-8")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("agent_slack.voice.subprocess.run", run)
    assert backend.transcribe(b"browser audio", ".webm") == "find the report"
    assert calls[0][0] == "ffmpeg"
    assert calls[0][calls[0].index("-ar") + 1] == "16000"
    assert calls[1][:4] == ["whisper-cli", "-m", str(model), "-f"]


def test_tts_adapter_runs_on_backend_and_returns_audio(monkeypatch):
    backend = LocalVoiceBackend()
    monkeypatch.setattr(backend, "_command_available", staticmethod(lambda _command: True))

    def run(command, **_kwargs):
        output = Path(command[command.index("-o") + 1]) if "-o" in command else Path(command[-1])
        output.write_bytes(b"backend audio")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("agent_slack.voice.subprocess.run", run)
    assert backend.synthesize("The answer", voice="Samantha") == b"backend audio"
