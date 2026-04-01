"""Configuration helpers for customer sequencer replication."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(slots=True)
class CustomerSequencerConfig:
    enabled: bool = False
    self_id: int = 0
    members: dict[int, str] = field(default_factory=dict)
    udp_bind_host: str = "0.0.0.0"
    udp_bind_port: int = 12001
    command_timeout_sec: float = 10.0
    heartbeat_interval_sec: float = 0.2
    retransmit_retry_sec: float = 0.3
    drop_probability: float = 0.0
    socket_timeout_sec: float = 0.1

    @property
    def majority(self) -> int:
        return len(self.members) // 2 + 1

    @classmethod
    def from_env(cls) -> "CustomerSequencerConfig":
        enabled_raw = os.getenv("CUSTOMER_SEQUENCER_ENABLED", "false").strip().lower()
        if enabled_raw not in {"true", "false"}:
            raise ValueError("CUSTOMER_SEQUENCER_ENABLED must be 'true' or 'false'")
        enabled = enabled_raw == "true"

        self_id = int(os.getenv("CUSTOMER_SEQUENCER_SELF_ID", "0"))
        members: dict[int, str] = {}
        for token in os.getenv("CUSTOMER_SEQUENCER_MEMBERS", "").split(","):
            item = token.strip()
            if not item:
                continue
            node_id_raw, sep, addr = item.partition("=")
            addr = addr.strip()
            if not sep or ":" not in addr:
                raise ValueError("CUSTOMER_SEQUENCER_MEMBERS entries must use id=host:port")
            members[int(node_id_raw.strip())] = addr

        if enabled and not members:
            raise ValueError(
                "CUSTOMER_SEQUENCER_ENABLED=true requires CUSTOMER_SEQUENCER_MEMBERS"
            )
        if not members:
            host = os.getenv("CUSTOMER_SEQUENCER_UDP_BIND_HOST", "127.0.0.1").strip()
            port = int(os.getenv("CUSTOMER_SEQUENCER_UDP_BIND_PORT", "12001"))
            members = {self_id: f"{host}:{port}"}
        if self_id not in members:
            raise ValueError(
                f"CUSTOMER_SEQUENCER_SELF_ID={self_id} not present in CUSTOMER_SEQUENCER_MEMBERS"
            )

        bind_host = os.getenv("CUSTOMER_SEQUENCER_UDP_BIND_HOST", "0.0.0.0").strip()
        bind_port = int(
            os.getenv(
                "CUSTOMER_SEQUENCER_UDP_BIND_PORT",
                members[self_id].rsplit(":", 1)[1],
            )
        )
        return cls(
            enabled=enabled,
            self_id=self_id,
            members=members,
            udp_bind_host=bind_host,
            udp_bind_port=bind_port,
            command_timeout_sec=float(os.getenv("CUSTOMER_SEQUENCER_COMMAND_TIMEOUT_SEC", "10.0")),
            heartbeat_interval_sec=float(os.getenv("CUSTOMER_SEQUENCER_HEARTBEAT_SEC", "0.2")),
            retransmit_retry_sec=float(os.getenv("CUSTOMER_SEQUENCER_RETRY_SEC", "0.3")),
            drop_probability=float(os.getenv("CUSTOMER_SEQUENCER_DROP_PROB", "0.0")),
            socket_timeout_sec=float(os.getenv("CUSTOMER_SEQUENCER_SOCKET_TIMEOUT_SEC", "0.1")),
        )
