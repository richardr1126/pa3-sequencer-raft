"""Raft runtime helpers for Product DB.

This module owns PySyncObj integration, raft env/config parsing,
write apply dispatch, and follower-to-leader read forwarding.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import grpc
from pysyncobj import SyncObj, SyncObjConf, replicated

from databases.product.db import default_persistence_root, persistence_paths_for_raft_node

FORWARDED_READ_HEADER = "x-productdb-forwarded-read"


@dataclass(slots=True)
class ProductRaftConfig:
    self_addr: str
    partner_addrs: list[str]
    command_timeout_sec: float = 3.0
    grpc_port_offset: int = 300
    full_dump_file: str | None = None
    journal_file: str | None = None


class RaftError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        grpc_status: grpc.StatusCode = grpc.StatusCode.UNAVAILABLE,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.grpc_status = grpc_status


def parse_raft_partner_addrs(raw: str, self_addr: str) -> list[str]:
    return [
        addr.strip()
        for addr in raw.split(",")
        if addr.strip() and addr.strip() != self_addr
    ]


def derive_grpc_target_from_raft_addr(raft_addr: str, port_offset: int) -> str | None:
    addr = raft_addr.strip()
    if ":" not in addr:
        return None
    host, port_raw = addr.rsplit(":", 1)
    if not port_raw.isdigit():
        return None
    return f"{host}:{int(port_raw) + port_offset}"


def build_raft_config_from_env() -> tuple[ProductRaftConfig, Path]:
    raft_self_addr = os.getenv("RAFT_SELF_ADDR", "127.0.0.1:11000").strip()
    raft_partner_addrs = parse_raft_partner_addrs(
        os.getenv("RAFT_PARTNER_ADDRS", ""),
        raft_self_addr,
    )
    grpc_port_offset = int(os.getenv("RAFT_GRPC_PORT_OFFSET", "300"))
    command_timeout_sec = float(os.getenv("RAFT_COMMAND_TIMEOUT_SEC", "3.0"))
    persistence_root = Path(os.getenv("PERSISTENCE_DIR", default_persistence_root()))
    sqlite_path, raft_full_dump_file, raft_journal_file = persistence_paths_for_raft_node(
        persistence_root,
        raft_self_addr,
    )

    raft_full_dump_file.parent.mkdir(parents=True, exist_ok=True)
    raft_journal_file.parent.mkdir(parents=True, exist_ok=True)

    return (
        ProductRaftConfig(
            self_addr=raft_self_addr,
            partner_addrs=raft_partner_addrs,
            command_timeout_sec=command_timeout_sec,
            grpc_port_offset=grpc_port_offset,
            full_dump_file=str(raft_full_dump_file),
            journal_file=str(raft_journal_file),
        ),
        sqlite_path,
    )


class ProductRaftStateMachine(SyncObj):
    def __init__(
        self,
        *,
        self_addr: str,
        partner_addrs: list[str],
        apply_fn: Callable[[dict[str, Any]], dict[str, Any]],
        full_dump_file: str | None,
        journal_file: str | None,
    ):
        self._lock = threading.RLock()
        self._apply_fn = apply_fn

        conf = SyncObjConf(
            dynamicMembershipChange=False,
            fullDumpFile=full_dump_file,
            journalFile=journal_file,
        )
        super().__init__(self_addr, partner_addrs, conf=conf)

    @replicated
    def apply_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            try:
                value = self._apply_fn(payload)
                return {"ok": True, "value": value}
            except RaftError as exc:
                return {
                    "ok": False,
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "grpc_status": exc.grpc_status.name,
                    },
                }
            except ValueError as exc:
                return {
                    "ok": False,
                    "error": {
                        "code": "INVALID_ARGUMENT",
                        "message": str(exc),
                        "grpc_status": grpc.StatusCode.INVALID_ARGUMENT.name,
                    },
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "error": {
                        "code": "RAFT_APPLY_ERROR",
                        "message": str(exc),
                        "grpc_status": grpc.StatusCode.INTERNAL.name,
                    },
                }


class ProductRaftNode:
    def __init__(
        self,
        config: ProductRaftConfig,
        apply_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ):
        self.config = config
        self._sm = ProductRaftStateMachine(
            self_addr=config.self_addr,
            partner_addrs=config.partner_addrs,
            apply_fn=apply_fn,
            full_dump_file=config.full_dump_file,
            journal_file=config.journal_file,
        )
        self.running = True

    def stop(self) -> None:
        self.running = False
        self._sm.destroy()

    def is_ready(self) -> bool:
        return self.running and self._sm.isReady()

    def _wait_until_ready(self, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self._sm.isReady():
                return True
            time.sleep(0.01)
        return self._sm.isReady()

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.running:
            raise RaftError("RAFT_STOPPED", "Raft node is stopped")
        if not self._wait_until_ready(self.config.command_timeout_sec):
            raise RaftError("RAFT_NOT_READY", "Raft node is not ready")

        try:
            result = self._sm.apply_payload(
                payload,
                sync=True,
                timeout=self.config.command_timeout_sec,
            )
        except Exception as exc:
            raise RaftError(
                "RAFT_UNAVAILABLE",
                f"Raft command failed to commit: {exc}",
                grpc.StatusCode.UNAVAILABLE,
            ) from exc

        if not isinstance(result, dict):
            raise RaftError(
                "RAFT_UNAVAILABLE",
                "Raft command returned no commit result",
                grpc.StatusCode.UNAVAILABLE,
            )

        if result.get("ok") is False:
            error = result.get("error") or {}
            status_name = str(error.get("grpc_status") or grpc.StatusCode.INTERNAL.name)
            try:
                grpc_status = grpc.StatusCode[status_name]
            except Exception:
                grpc_status = grpc.StatusCode.INTERNAL
            raise RaftError(
                code=str(error.get("code") or "RAFT_APPLY_ERROR"),
                message=str(error.get("message") or "Raft apply failed"),
                grpc_status=grpc_status,
            )

        return result.get("value") or {}

    def cluster_status(self) -> dict[str, Any]:
        if not self.running:
            return {
                "self_addr": self.config.self_addr,
                "leader_addr": None,
                "has_quorum": False,
                "is_leader": False,
            }

        try:
            raw = self._sm.getStatus()
        except Exception:
            return {
                "self_addr": self.config.self_addr,
                "leader_addr": None,
                "has_quorum": False,
                "is_leader": False,
            }

        self_node = raw.get("self")
        leader_node = raw.get("leader")
        self_addr = self.config.self_addr if self_node is None else self_node.id
        leader_addr = None if leader_node is None else leader_node.id
        has_quorum = bool(raw.get("has_quorum"))
        return {
            "self_addr": self_addr,
            "leader_addr": leader_addr,
            "has_quorum": has_quorum,
            "is_leader": leader_addr is not None and leader_addr == self_addr,
        }

    def is_leader(self) -> bool:
        return bool(self.cluster_status().get("is_leader"))

    def leader_addr(self) -> str | None:
        value = self.cluster_status().get("leader_addr")
        return str(value) if value else None

    def leader_grpc_target(self) -> str | None:
        leader_addr = self.leader_addr()
        if not leader_addr:
            return None
        return derive_grpc_target_from_raft_addr(
            leader_addr,
            port_offset=self.config.grpc_port_offset,
        )

    def get_leader_grpc_target_or_error(self, *, already_forwarded: bool = False) -> str | None:
        if not self.is_ready():
            raise RaftError("RAFT_NOT_READY", "Raft node is not ready")

        status = self.cluster_status()
        if not status.get("has_quorum"):
            raise RaftError(
                "RAFT_NO_QUORUM",
                "Raft cluster has no quorum for leader-consistent reads",
                grpc.StatusCode.UNAVAILABLE,
            )

        if status.get("is_leader"):
            return None

        if already_forwarded:
            raise RaftError(
                "LEADER_FORWARD_LOOP",
                "Forwarded read landed on follower; leader changed or mapping is stale",
                grpc.StatusCode.UNAVAILABLE,
            )

        target = self.leader_grpc_target()
        if target:
            return target

        raise RaftError(
            "LEADER_GRPC_UNKNOWN",
            "Leader gRPC address is not configured for read forwarding",
            grpc.StatusCode.UNAVAILABLE,
        )


class ProductReadForwarder:
    def __init__(self, raft_node: ProductRaftNode):
        self.raft = raft_node
        self._lock = threading.Lock()
        self._channels: dict[str, grpc.Channel] = {}
        self._stubs: dict[str, Any] = {}

    def close(self) -> None:
        with self._lock:
            for channel in self._channels.values():
                channel.close()
            self._channels.clear()
            self._stubs.clear()

    @staticmethod
    def _is_forwarded_request(context: grpc.ServicerContext) -> bool:
        for metadata in context.invocation_metadata():
            if metadata.key.lower() == FORWARDED_READ_HEADER and metadata.value == "1":
                return True
        return False

    def _get_stub(self, target: str):
        from common.grpc_gen import product_db_pb2_grpc

        with self._lock:
            stub = self._stubs.get(target)
            if stub is not None:
                return stub
            channel = grpc.insecure_channel(target)
            stub = product_db_pb2_grpc.ProductDbServiceStub(channel)
            self._channels[target] = channel
            self._stubs[target] = stub
            return stub

    def forward_if_needed(
        self,
        context: grpc.ServicerContext,
        rpc_name: str,
        request: Any,
    ) -> Any | None:
        target = self.raft.get_leader_grpc_target_or_error(
            already_forwarded=self._is_forwarded_request(context)
        )
        if target is None:
            return None

        stub = self._get_stub(target)
        rpc = getattr(stub, rpc_name)
        try:
            return rpc(
                request,
                timeout=self.raft.config.command_timeout_sec,
                metadata=[(FORWARDED_READ_HEADER, "1")],
            )
        except grpc.RpcError as exc:
            raise RaftError(
                "LEADER_FORWARD_FAILED",
                f"Failed to forward read to leader: {exc.details() or exc}",
                exc.code() if isinstance(exc.code(), grpc.StatusCode) else grpc.StatusCode.UNAVAILABLE,
            ) from exc
