from __future__ import annotations

import base64
import hashlib

from agent_slack.realtime.protocol import accept_key
from agent_slack.realtime.session import VoiceSession, VoiceState


class FakeTransport:
    def __init__(self) -> None:
        self.json_events: list[dict] = []
        self.binary_frames: list[bytes] = []

    def send_json(self, payload: dict) -> None:
        self.json_events.append(payload)

    def send_binary(self, data: bytes) -> None:
        self.binary_frames.append(data)

    def types(self) -> list[str]:
        return [event["type"] for event in self.json_events]


class FakeVoiceBackend:
    def __init__(self, transcript: str = "hello there", on_synthesize=None) -> None:
        self.transcript = transcript
        self.on_synthesize = on_synthesize

    def transcribe(self, audio: bytes, suffix: str) -> str:
        return self.transcript

    def synthesize(self, text: str) -> bytes:
        if self.on_synthesize:
            self.on_synthesize()
        return b"FAKE-WAV-AUDIO"


class FakeChatEngine:
    """Mimics ChatEngine.stream_message's event shape without touching a real CLI."""

    def __init__(self) -> None:
        self.cancelled: set[str] = set()
        self.sessions: dict[str, object] = {}
        self._session_counter = 0
        self._response_counter = 0

    def create_session(self, server_id: str, title: str = "Voice chat"):
        self._session_counter += 1
        session_id = f"session-{self._session_counter}"
        session = type("S", (), {"id": session_id})()
        self.sessions[session_id] = session
        return session

    def cancel(self, response_id: str) -> None:
        self.cancelled.add(response_id)

    def stream_message(self, session_id: str, text: str, modality: str = "text"):
        self._response_counter += 1
        response_id = f"resp-{self._response_counter}"
        yield {"type": "response_started", "response_id": response_id}
        yield {"type": "retrieval_started", "response_id": response_id, "strategy": "semantic"}
        yield {"type": "retrieval_finished", "response_id": response_id, "sources": []}
        if response_id in self.cancelled:
            yield {"type": "assistant_interrupted", "response_id": response_id}
            return
        yield {"type": "assistant_text_delta", "response_id": response_id, "text": "Hi"}
        if response_id in self.cancelled:
            yield {"type": "assistant_interrupted", "response_id": response_id}
            return
        yield {"type": "assistant_completed", "response_id": response_id, "text": "Hi"}


def _wav(payload: bytes = b"pcm") -> bytes:
    return payload


def test_websocket_accept_key_matches_rfc6455_example() -> None:
    # RFC 6455 section 1.3 worked example.
    assert accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_voice_session_normal_turn_completes_with_audio() -> None:
    transport = FakeTransport()
    engine = FakeChatEngine()
    session = VoiceSession(transport, engine, FakeVoiceBackend(), server_id="srv1")

    session.handle_control({"type": "session.start"})
    session.handle_audio_chunk(_wav())
    session._commit_audio()  # run synchronously in the test instead of a background thread

    assert transport.types() == [
        "session.ready",
        "user.speech_started",
        "user.speech_stopped",
        "user.transcript_final",
        "assistant.started",
        "retrieval.started",
        "retrieval.finished",
        "assistant.text_delta",
        "assistant.audio_delta",
        "assistant.completed",
    ]
    assert transport.binary_frames == [b"FAKE-WAV-AUDIO"]
    assert session.state == VoiceState.LISTENING


def test_barge_in_cancels_response_and_rejects_stale_audio() -> None:
    transport = FakeTransport()
    engine = FakeChatEngine()

    barged_in = {"done": False}

    def synthesize_then_barge_in():
        # Simulate the user starting to talk while TTS is being generated for
        # response A: this must discard A's audio and let a new turn (B) start.
        if not barged_in["done"]:
            barged_in["done"] = True
            session.handle_audio_chunk(_wav(b"user-interrupts"))

    backend = FakeVoiceBackend(on_synthesize=synthesize_then_barge_in)
    session = VoiceSession(transport, engine, backend, server_id="srv1")
    session.handle_control({"type": "session.start"})
    session.handle_audio_chunk(_wav())
    session._commit_audio()

    # The stale response's audio must never have been sent.
    assert transport.binary_frames == []
    assert "assistant.interrupted" in transport.types()
    assert "resp-1" in engine.cancelled

    # A fresh turn (B) must still work after the interruption - the interrupting
    # audio itself becomes the start of the new user turn.
    transport.json_events.clear()
    session._commit_audio()
    assert "user.transcript_final" in transport.types()
    assert "assistant.completed" in transport.types()


def test_response_cancel_control_message_marks_response_interrupted() -> None:
    transport = FakeTransport()
    engine = FakeChatEngine()
    session = VoiceSession(transport, engine, FakeVoiceBackend(), server_id="srv1")
    session.handle_control({"type": "session.start"})
    session.current_response_id = "resp-1"

    session.handle_control({"type": "response.cancel", "response_id": "resp-1"})

    assert "resp-1" in engine.cancelled
    assert "resp-1" in session._interrupted_response_ids


def test_session_end_stops_the_loop() -> None:
    transport = FakeTransport()
    session = VoiceSession(transport, FakeChatEngine(), FakeVoiceBackend(), server_id="srv1")
    assert session.handle_control({"type": "session.end"}) is False
    assert session.handle_control({"type": "session.start"}) is True
