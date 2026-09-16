from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


class VoiceBackendError(RuntimeError):
    pass


class LocalVoiceBackend:
    """Small process adapter for host-local ASR/TTS; clients never call providers."""

    def __init__(self) -> None:
        self.whisper_cli = os.getenv("AGENT_SLACK_WHISPER_CLI", "whisper-cli")
        self.whisper_model = os.getenv("AGENT_SLACK_WHISPER_MODEL", "").strip()
        self.ffmpeg = os.getenv("AGENT_SLACK_FFMPEG", "ffmpeg")
        self.tts_command = os.getenv("AGENT_SLACK_TTS_COMMAND", "say").strip() or "say"

    def capabilities(self) -> dict[str, object]:
        return {
            "asr": {
                "provider": "whisper.cpp",
                "available": bool(self.whisper_model) and Path(self.whisper_model).is_file() and self._command_available(self.whisper_cli),
                "local": True,
                "streaming": False,
                "model": self.whisper_model or None,
                "accepted_formats": ["wav", "flac", "mp3", "ogg", "webm", "m4a"],
            },
            "tts": {
                "provider": "macOS say" if self.tts_command == "say" else "configured command",
                "available": self._command_available(self.tts_command),
                "local": True,
                "streaming": False,
                "audio_format": "wav",
            },
        }

    def transcribe(self, audio: bytes, suffix: str = ".wav") -> str:
        if not self.whisper_model:
            raise VoiceBackendError("Configure AGENT_SLACK_WHISPER_MODEL for backend ASR")
        if not Path(self.whisper_model).is_file():
            raise VoiceBackendError("AGENT_SLACK_WHISPER_MODEL does not point to a backend model file")
        if not self._command_available(self.whisper_cli):
            raise VoiceBackendError("whisper-cli is not installed on the backend")
        with tempfile.TemporaryDirectory(prefix="agent-slack-asr-") as directory:
            root = Path(directory)
            audio_path = root / ("input" + (suffix if suffix.startswith(".") else ".wav"))
            output_path = root / "transcript"
            audio_path.write_bytes(audio)
            input_path = audio_path
            if audio_path.suffix.casefold() not in {".wav", ".flac", ".mp3", ".ogg"}:
                if not self._command_available(self.ffmpeg):
                    raise VoiceBackendError("ffmpeg is required to convert browser/mobile audio")
                input_path = root / "converted.wav"
                converted = subprocess.run(
                    [self.ffmpeg, "-y", "-i", str(audio_path), "-ar", "16000", "-ac", "1", str(input_path)],
                    capture_output=True, text=True, timeout=120, check=False,
                )
                if converted.returncode != 0:
                    raise VoiceBackendError(converted.stderr.strip() or "audio conversion failed")
            command = [
                self.whisper_cli,
                "-m", self.whisper_model,
                "-f", str(input_path),
                "-oj", "-np", "-nt",
                "-of", str(output_path),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
            if result.returncode != 0:
                raise VoiceBackendError(result.stderr.strip() or "whisper.cpp transcription failed")
            json_path = output_path.with_suffix(".json")
            if json_path.is_file():
                try:
                    payload = json.loads(json_path.read_text(encoding="utf-8"))
                    text = payload.get("transcription") or payload.get("text") or ""
                    if isinstance(text, list):
                        text = " ".join(str(item.get("text", "")) for item in text if isinstance(item, dict))
                    return str(text).strip()
                except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise VoiceBackendError("whisper.cpp returned invalid JSON") from exc
            return result.stdout.strip()

    def synthesize(self, text: str, voice: str | None = None) -> bytes:
        if not self._command_available(self.tts_command):
            raise VoiceBackendError("Backend TTS command is not installed")
        cleaned = str(text or "").strip()
        if not cleaned:
            raise VoiceBackendError("text is required for synthesis")
        with tempfile.TemporaryDirectory(prefix="agent-slack-tts-") as directory:
            source = Path(directory) / "speech.aiff"
            output = Path(directory) / "speech.wav"
            command = [self.tts_command]
            if voice:
                command += ["-v", voice]
            command += ["-o", str(source), cleaned]
            result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
            if result.returncode != 0 or not source.is_file():
                raise VoiceBackendError(result.stderr.strip() or "backend TTS failed")
            if not self._command_available(self.ffmpeg):
                raise VoiceBackendError("ffmpeg is required to convert backend speech to browser-compatible WAV")
            converted = subprocess.run(
                [self.ffmpeg, "-y", "-i", str(source), "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(output)],
                capture_output=True, text=True, timeout=120, check=False,
            )
            if converted.returncode != 0 or not output.is_file():
                raise VoiceBackendError(converted.stderr.strip() or "backend speech conversion failed")
            return output.read_bytes()

    @staticmethod
    def _command_available(command: str) -> bool:
        path = Path(command)
        if path.is_absolute():
            return path.is_file()
        return any((directory / command).is_file() for directory in map(Path, os.getenv("PATH", "").split(os.pathsep)))
