from __future__ import annotations

import threading
from enum import Enum
from typing import Any, Protocol
from uuid import uuid4


class VoiceState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    THINKING = "thinking"
    ASSISTANT_SPEAKING = "assistant_speaking"
    INTERRUPTING = "interrupting"


class VoiceTransport(Protocol):
    def send_json(self, payload: dict) -> None: ...
    def send_binary(self, data: bytes) -> None: ...


class VoiceSession:
    """Full-duplex voice session state machine, backed by the same ChatEngine as text.

    Honest limitation: the local ASR (whisper.cpp) and TTS (`say`) backends are
    process-per-utterance, not streaming, so real first-audio/interruption latency
    will exceed the <200ms target in section 42. The protocol, response_id
    bookkeeping, and stale-audio rejection below are fully real; only "continuous
    low-latency streaming" from the local model backends is not achievable without
    a streaming ASR/TTS engine.
    """

    def __init__(self, transport: VoiceTransport, chat_engine, voice_backend, server_id: str) -> None:
        self.transport = transport
        self.chat_engine = chat_engine
        self.voice_backend = voice_backend
        self.server_id = server_id
        self.state = VoiceState.IDLE
        self.chat_session = None
        self._audio_buffer = bytearray()
        self.current_response_id: str | None = None
        self._interrupted_response_ids: set[str] = set()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    # --- client -> server -------------------------------------------------

    def handle_control(self, event: dict[str, Any]) -> bool:
        """Returns False when the session should end."""
        event_type = event.get("type")
        if event_type == "session.start":
            self._start(event)
        elif event_type == "audio.commit":
            self._worker = threading.Thread(target=self._commit_audio, daemon=True)
            self._worker.start()
        elif event_type == "response.cancel":
            self._cancel_response(str(event.get("response_id") or self.current_response_id or ""))
        elif event_type == "session.end":
            return False
        return True

    def handle_audio_chunk(self, data: bytes) -> None:
        with self._lock:
            if self.state == VoiceState.ASSISTANT_SPEAKING:
                self._barge_in()
            if self.state not in (VoiceState.LISTENING, VoiceState.USER_SPEAKING):
                return
            if self.state == VoiceState.LISTENING:
                self.state = VoiceState.USER_SPEAKING
                self.transport.send_json({"type": "user.speech_started"})
            self._audio_buffer.extend(data)

    # --- state transitions --------------------------------------------------

    def _start(self, event: dict[str, Any]) -> None:
        source_chat_id = str(event.get("source_chat_id") or "")
        self.chat_session = self.chat_engine.create_session(self.server_id, title="Voice chat")
        self.state = VoiceState.LISTENING
        self.transport.send_json({
            "type": "session.ready",
            "session_id": self.chat_session.id,
            "source_chat_id": source_chat_id,
        })

    def _return_to_listening(self) -> None:
        """Go back to LISTENING unless a barge-in already claimed USER_SPEAKING."""
        if self.state not in (VoiceState.USER_SPEAKING, VoiceState.INTERRUPTING):
            self.state = VoiceState.LISTENING

    def _barge_in(self) -> None:
        self.state = VoiceState.INTERRUPTING
        if self.current_response_id:
            self.chat_engine.cancel(self.current_response_id)
            self._interrupted_response_ids.add(self.current_response_id)
            self.transport.send_json({"type": "assistant.interrupted", "response_id": self.current_response_id})
        self.current_response_id = None
        self.state = VoiceState.USER_SPEAKING
        self.transport.send_json({"type": "user.speech_started"})

    def _commit_audio(self) -> None:
        with self._lock:
            if not self._audio_buffer or self.chat_session is None:
                return
            audio = bytes(self._audio_buffer)
            self._audio_buffer = bytearray()
            self.state = VoiceState.THINKING
        self.transport.send_json({"type": "user.speech_stopped"})
        try:
            text = self.voice_backend.transcribe(audio, ".wav")
        except Exception as exc:  # backend raises RuntimeError subclasses on failure
            self.transport.send_json({"type": "error", "message": str(exc)})
            self._return_to_listening()
            return
        if not text:
            self._return_to_listening()
            return
        self.transport.send_json({"type": "user.transcript_final", "text": text})
        self._run_turn(text)

    def _run_turn(self, text: str) -> None:
        response_id_map: dict[str, str] = {}
        final_text = ""
        for event in self.chat_engine.stream_message(self.chat_session.id, text, modality="voice"):
            event_type = event["type"]
            engine_response_id = event.get("response_id", "")
            response_id = response_id_map.setdefault(engine_response_id, engine_response_id or uuid4().hex[:12])
            if response_id in self._interrupted_response_ids:
                continue
            if event_type == "response_started":
                self.current_response_id = response_id
                self.state = VoiceState.THINKING
                self.transport.send_json({"type": "assistant.started", "response_id": response_id})
            elif event_type == "retrieval_started":
                self.transport.send_json({"type": "retrieval.started", "response_id": response_id})
            elif event_type == "retrieval_finished":
                self.transport.send_json({
                    "type": "retrieval.finished", "response_id": response_id, "sources": event.get("sources", []),
                })
            elif event_type == "assistant_text_delta":
                final_text += event["text"]
                self.transport.send_json({"type": "assistant.text_delta", "response_id": response_id, "text": event["text"]})
            elif event_type == "assistant_interrupted":
                self.transport.send_json({"type": "assistant.interrupted", "response_id": response_id})
                self._return_to_listening()
                return
            elif event_type == "error":
                self.transport.send_json({"type": "error", "response_id": response_id, "message": event.get("message", "")})
                self._return_to_listening()
                return
            elif event_type == "assistant_completed":
                self._speak(response_id, event.get("text") or final_text)

        self._return_to_listening()

    def _speak(self, response_id: str, text: str) -> None:
        if response_id in self._interrupted_response_ids or not text.strip():
            self.transport.send_json({"type": "assistant.completed", "response_id": response_id})
            return
        self.state = VoiceState.ASSISTANT_SPEAKING
        try:
            audio = self.voice_backend.synthesize(text)
        except Exception as exc:
            self.transport.send_json({"type": "error", "response_id": response_id, "message": str(exc)})
            self._return_to_listening()
            return
        if response_id in self._interrupted_response_ids:
            return  # stale: barge-in happened while synthesizing
        self.transport.send_binary(audio)
        self.transport.send_json({"type": "assistant.audio_delta", "response_id": response_id, "bytes": len(audio)})
        self.transport.send_json({"type": "assistant.completed", "response_id": response_id})
        self._return_to_listening()

    def _cancel_response(self, response_id: str) -> None:
        if not response_id:
            return
        self.chat_engine.cancel(response_id)
        self._interrupted_response_ids.add(response_id)
