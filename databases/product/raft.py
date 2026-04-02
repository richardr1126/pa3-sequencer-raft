"""Raft runtime helpers for Product DB.

This module owns PySyncObj integration, raft env/config parsing,
and write apply dispatch.
"""

import logging
import os
import pickle
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import grpc
from pysyncobj import SyncObj, SyncObjConf, replicated

from databases.product.db import default_persistence_root, persistence_paths_for_raft_node


@dataclass(slots=True)
class ProductRaftConfig:
    self_addr: str
    partner_addrs: list[str]
    command_timeout_sec: float = 3.0
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


def build_raft_config_from_env() -> tuple[ProductRaftConfig, Path]:
    raft_self_addr = os.getenv("RAFT_SELF_ADDR", "127.0.0.1:11000").strip()
    raft_partner_addrs = parse_raft_partner_addrs(
        os.getenv("RAFT_PARTNER_ADDRS", ""),
        raft_self_addr,
    )
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
        apply_committed_write_fn: Callable[[dict[str, Any], int], dict[str, Any]],
        full_dump_file: str | None,
        journal_file: str | None,
    ):
        self._lock = threading.RLock()
        self._apply_fn = apply_committed_write_fn

        conf = SyncObjConf(
            dynamicMembershipChange=False,
            fullDumpFile=full_dump_file,
            journalFile=journal_file,
            appendEntriesPeriod=0.02,
        )
        super().__init__(self_addr, partner_addrs, conf=conf)

    @replicated
    def commit_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            try:
                raft_index = int(self.raftLastApplied) + 1
                return self._apply_fn(payload, raft_index)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": {
                        "code": "RAFT_APPLY_ERROR",
                        "message": str(exc),
                        "grpc_status": grpc.StatusCode.INTERNAL.name,
                    },
                }

    @staticmethod
    def _decode_regular_payload(command: bytes) -> dict[str, Any] | None:
        if not command or command[0] != 0:
            return None
        decoded = pickle.loads(command[1:])
        if not isinstance(decoded, tuple) or len(decoded) < 2:
            return None
        args = decoded[1]
        if not isinstance(args, tuple) or not args:
            return None
        payload = args[0]
        if not isinstance(payload, dict):
            return None
        return payload

    def replay_committed_writes(self, last_applied_sqlite: int) -> int:
        with self._lock:
            first_log_index = int(self._SyncObj__raftLog[0][1])  # type: ignore[attr-defined]
            start_index = max(int(last_applied_sqlite) + 1, first_log_index)
            commit_index = int(self.raftCommitIndex)
            if start_index > commit_index:
                return 0

            replayed = 0
            entries = self._SyncObj__getEntries(  # type: ignore[attr-defined]
                start_index,
                commit_index - start_index + 1,
            )
            for command, raft_index, _term in entries:
                payload = self._decode_regular_payload(command)
                if payload is None:
                    continue
                self._apply_fn(payload, int(raft_index))
                replayed += 1
            return replayed


class ProductRaftNode:
    def __init__(
        self,
        config: ProductRaftConfig,
        apply_committed_write_fn: Callable[[dict[str, Any], int], dict[str, Any]],
    ):
        self.config = config
        self._log = logging.getLogger(__name__)
        self._sm = ProductRaftStateMachine(
            self_addr=config.self_addr,
            partner_addrs=config.partner_addrs,
            apply_committed_write_fn=apply_committed_write_fn,
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

    def commit_write(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.running:
            raise RaftError("RAFT_STOPPED", "Raft node is stopped")
        if not self._wait_until_ready(self.config.command_timeout_sec):
            raise RaftError("RAFT_NOT_READY", "Raft node is not ready")

        started_at = time.monotonic()
        try:
            result = self._sm.commit_payload(
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
        duration_ms = (time.monotonic() - started_at) * 1000.0
        if duration_ms >= 200.0:
            self._log.warning(
                "product-raft slow commit: self=%s command=%s duration_ms=%.1f",
                self.config.self_addr,
                payload.get("type", "<unknown>"),
                duration_ms,
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
                message=str(error.get("message") or "Raft commit failed"),
                grpc_status=grpc_status,
            )

        return result.get("value") or {}

    def replay_committed_writes(self, last_applied_sqlite: int) -> int:
        replay_timeout_sec = max(self.config.command_timeout_sec, 30.0)
        if not self._wait_until_ready(replay_timeout_sec):
            raise RaftError(
                "RAFT_NOT_READY",
                "Raft node is not ready for startup sqlite replay",
                grpc.StatusCode.UNAVAILABLE,
            )
        replayed = self._sm.replay_committed_writes(last_applied_sqlite)
        if replayed > 0:
            self._log.info(
                "product-raft startup replay: self=%s from_index=%s replayed=%s",
                self.config.self_addr,
                last_applied_sqlite,
                replayed,
            )
        return replayed
