"""Shared in-memory protocol state for rotating sequencer replication."""

import threading
from dataclasses import dataclass, field
from typing import Any

from .messages import parse_request_key, request_key


@dataclass(slots=True)
class PendingCommand:
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Exception | None = None


@dataclass(slots=True)
class SenderTrack:
    request_locals_seen: set[int] = field(default_factory=set)
    highest_request_local_contiguous: int = -1
    highest_request_local_seen: int = -1
    assigned_locals: set[int] = field(default_factory=set)
    highest_assigned_local_contiguous: int = -1


class SequencerState:
    def __init__(self, *, members: dict[int, str], self_id: int):
        self.members = dict(members)
        self.self_id = self_id

        self.next_local_request_seq = 0

        self.request_payload_by_id: dict[str, dict[str, Any]] = {}
        self.request_message_cache: dict[tuple[int, int], bytes] = {}
        self.sender_tracks: dict[int, SenderTrack] = {
            member_id: SenderTrack() for member_id in self.members
        }

        self.sequence_request_id_by_global: dict[int, str] = {}
        self.sequence_message_cache: dict[int, bytes] = {}
        self.assigned_request_ids: set[str] = set()
        self.highest_contiguous_sequence = -1
        self.highest_seen_sequence = -1

        self.highest_contiguous_received_sequence = -1
        self.highest_contiguous_delivered_sequence = -1

        self.pending_local_commands: dict[str, PendingCommand] = {}
        self.delivered_request_ids: set[str] = set()

        self.peer_receive_upto_by_member: dict[int, int] = {
            member_id: -1 for member_id in self.members
        }

        self.last_retransmit_sequence_request_at: dict[int, float] = {}
        self.last_retransmit_request_at: dict[tuple[int, int], float] = {}

    def allocate_local_request_seq(self) -> int:
        seq = self.next_local_request_seq
        self.next_local_request_seq += 1
        return seq

    def local_receive_upto(self) -> int:
        return self.highest_contiguous_received_sequence

    def register_request(
        self,
        *,
        request_sender_id: int,
        request_local_seq: int,
        method: str,
        kwargs: dict[str, Any],
    ) -> tuple[str, list[int]]:
        req_id = request_key(request_sender_id, request_local_seq)

        if req_id not in self.request_payload_by_id:
            self.request_payload_by_id[req_id] = {
                "method": method,
                "kwargs": kwargs,
                "request_sender_id": request_sender_id,
                "request_local_seq": request_local_seq,
            }

        track = self.sender_tracks[request_sender_id]
        locals_seen = track.request_locals_seen
        prev_highest_contiguous = track.highest_request_local_contiguous

        # Track per-sender contiguous locals for gap detection and sequencing rules.
        locals_seen.add(request_local_seq)
        track.highest_request_local_seen = max(
            track.highest_request_local_seen,
            request_local_seq,
        )

        highest_contiguous = prev_highest_contiguous
        while highest_contiguous + 1 in locals_seen:
            highest_contiguous += 1
        track.highest_request_local_contiguous = highest_contiguous

        self.recompute_highest_contiguous_received_sequence()

        missing_locals: list[int] = []
        if request_local_seq > prev_highest_contiguous + 1:
            for local in range(prev_highest_contiguous + 1, request_local_seq):
                if local not in locals_seen:
                    missing_locals.append(local)

        return req_id, missing_locals

    def register_sequence(self, *, global_seq: int, request_id: str) -> bool:
        existing = self.sequence_request_id_by_global.get(global_seq)
        if existing is not None and existing != request_id:
            return False

        # Update sender-local assigned frontier when a request gets a global seq.
        self.sequence_request_id_by_global[global_seq] = request_id
        self.assigned_request_ids.add(request_id)

        self.highest_seen_sequence = max(self.highest_seen_sequence, global_seq)
        while self.sequence_request_id_by_global.get(self.highest_contiguous_sequence + 1):
            self.highest_contiguous_sequence += 1

        req_sender_id, req_local_seq = parse_request_key(request_id)
        track = self.sender_tracks[req_sender_id]
        assigned_locals = track.assigned_locals
        assigned_locals.add(req_local_seq)

        highest_assigned_contiguous = track.highest_assigned_local_contiguous
        while highest_assigned_contiguous + 1 in assigned_locals:
            highest_assigned_contiguous += 1
        track.highest_assigned_local_contiguous = highest_assigned_contiguous

        self.recompute_highest_contiguous_received_sequence()
        return True

    def recompute_highest_contiguous_received_sequence(self) -> None:
        # Advance recv_upto only when both sequence and payload exist at next global.
        while True:
            next_global = self.highest_contiguous_received_sequence + 1
            req_id = self.sequence_request_id_by_global.get(next_global)
            if req_id is None:
                return
            if req_id not in self.request_payload_by_id:
                return
            self.highest_contiguous_received_sequence = next_global

    def pick_sequence_candidate_request_id(self) -> str | None:
        candidates: list[tuple[int, int, str]] = []

        for request_id, payload in self.request_payload_by_id.items():
            if request_id in self.delivered_request_ids:
                continue
            if request_id in self.assigned_request_ids:
                continue

            sender_id = int(payload["request_sender_id"])
            local_seq = int(payload["request_local_seq"])
            # Only sequence the next contiguous local request per sender.
            expected_local = (
                self.sender_tracks[sender_id].highest_assigned_local_contiguous + 1
            )
            if local_seq != expected_local:
                continue

            candidates.append((sender_id, local_seq, request_id))

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x[1], x[0]))
        return candidates[0][2]

    def next_deliverable(self, *, majority: int) -> tuple[int, str, dict[str, Any]] | None:
        next_global = self.highest_contiguous_delivered_sequence + 1
        req_id = self.sequence_request_id_by_global.get(next_global)
        if req_id is None:
            return None

        payload = self.request_payload_by_id.get(req_id)
        if payload is None:
            return None

        ready_count = 0
        for member_id in self.members:
            if self.peer_receive_upto_by_member.get(member_id, -1) >= next_global:
                ready_count += 1

        # Deliver only after majority evidence peers reached this global sequence.
        if ready_count < majority:
            return None

        return next_global, req_id, payload

    def mark_delivered(
        self,
        *,
        global_seq: int,
        request_id: str,
        result: dict[str, Any] | None,
        error: Exception | None,
    ) -> None:
        if self.highest_contiguous_delivered_sequence + 1 == global_seq:
            self.highest_contiguous_delivered_sequence = global_seq

        self.delivered_request_ids.add(request_id)

        pending = self.pending_local_commands.get(request_id)
        if pending is not None:
            pending.result = result
            pending.error = error
            pending.event.set()
