from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
from dataclasses import dataclass

WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


class ConnectionClosed(Exception):
    pass


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + WEBSOCKET_GUID).encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def handshake_response(client_key: str) -> bytes:
    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Accept: {accept_key(client_key)}",
        "", "",
    ]
    return "\r\n".join(lines).encode("utf-8")


@dataclass
class Frame:
    opcode: int
    payload: bytes
    fin: bool = True


class WebSocketConnection:
    """Minimal RFC 6455 server-side connection: no extensions, no fragmentation on send."""

    def __init__(self, raw_socket: socket.socket) -> None:
        self.socket = raw_socket
        self._buffer = b""
        self._send_lock = threading.Lock()

    def send_text(self, text: str) -> None:
        self._send_frame(OPCODE_TEXT, text.encode("utf-8"))

    def send_json(self, payload: dict) -> None:
        self.send_text(json.dumps(payload))

    def send_binary(self, data: bytes) -> None:
        self._send_frame(OPCODE_BINARY, data)

    def close(self, code: int = 1000) -> None:
        try:
            self._send_frame(OPCODE_CLOSE, struct.pack("!H", code))
        except OSError:
            pass
        finally:
            try:
                self.socket.close()
            except OSError:
                pass

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([length])
        elif length < 65536:
            header += bytes([126]) + struct.pack("!H", length)
        else:
            header += bytes([127]) + struct.pack("!Q", length)
        with self._send_lock:
            self.socket.sendall(header + payload)

    def recv_frame(self) -> Frame:
        first_two = self._read_exact(2)
        fin = bool(first_two[0] & 0x80)
        opcode = first_two[0] & 0x0F
        masked = bool(first_two[1] & 0x80)
        length = first_two[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        mask_key = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
        return Frame(opcode=opcode, payload=payload, fin=fin)

    def recv_message(self) -> Frame:
        """Reassemble one logical message, replying to ping/close automatically."""
        parts: list[bytes] = []
        opcode = None
        while True:
            frame = self.recv_frame()
            if frame.opcode == OPCODE_CLOSE:
                self.close()
                raise ConnectionClosed()
            if frame.opcode == OPCODE_PING:
                self._send_frame(OPCODE_PONG, frame.payload)
                continue
            if frame.opcode == OPCODE_PONG:
                continue
            if opcode is None:
                opcode = frame.opcode
            parts.append(frame.payload)
            if frame.fin:
                break
        return Frame(opcode=opcode or OPCODE_TEXT, payload=b"".join(parts))

    def _read_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self.socket.recv(max(count - len(self._buffer), 4096))
            if not chunk:
                raise ConnectionClosed()
            self._buffer += chunk
        result, self._buffer = self._buffer[:count], self._buffer[count:]
        return result
