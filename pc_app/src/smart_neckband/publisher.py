from __future__ import annotations

from typing import Protocol

from .protocol import ParsedPacket


class DataPublisher(Protocol):
    def publish_packet(self, packet: ParsedPacket) -> None:
        """Publish a parsed packet to an optional downstream consumer."""


class NullPublisher:
    def publish_packet(self, packet: ParsedPacket) -> None:
        del packet
