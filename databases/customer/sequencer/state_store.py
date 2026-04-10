"""Shared in-memory protocol state for rotating sequencer replication."""

import heapq
import random
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
    def __init__(
        self,
        *,
        members: dict[int, str],
        self_id: int,
    ):
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
        self.sequence_retransmit_retry_interval_sec: dict[int, float] = {}
        self.request_retransmit_retry_interval_sec: dict[tuple[int, int], float] = {}

        # Min-heap candidate index: (local_seq, sender_id, request_id).
        self._ready_candidates_heap: list[tuple[int, int, str]] = []
        self._ready_candidate_by_sender: dict[int, str] = {}

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

        track = self.sender_tracks[request_sender_id]
        if (
            request_local_seq <= track.highest_assigned_local_contiguous
            and req_id not in self.request_payload_by_id
            and req_id not in self.assigned_request_ids
        ):
            # Already assigned (and potentially compacted); ignore stale duplicates.
            return req_id, []

        if req_id not in self.request_payload_by_id:
            self.request_payload_by_id[req_id] = {
                "method": method,
                "kwargs": kwargs,
                "request_sender_id": request_sender_id,
                "request_local_seq": request_local_seq,
            }

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

        self._refresh_sender_ready_candidate(request_sender_id)
        return req_id, missing_locals

    def register_sequence(self, *, global_seq: int, request_id: str) -> bool:
        if global_seq <= self.highest_contiguous_delivered_sequence:
            return False
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
        self._refresh_sender_ready_candidate(req_sender_id)
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
        while self._ready_candidates_heap:
            local_seq, sender_id, request_id = heapq.heappop(self._ready_candidates_heap)
            current = self._ready_candidate_by_sender.get(sender_id)
            if current != request_id:
                continue

            payload = self.request_payload_by_id.get(request_id)
            if payload is None:
                self._ready_candidate_by_sender.pop(sender_id, None)
                self._refresh_sender_ready_candidate(sender_id)
                continue
            if request_id in self.delivered_request_ids or request_id in self.assigned_request_ids:
                self._ready_candidate_by_sender.pop(sender_id, None)
                self._refresh_sender_ready_candidate(sender_id)
                continue

            expected_local = self.sender_tracks[sender_id].highest_assigned_local_contiguous + 1
            if local_seq != expected_local:
                self._ready_candidate_by_sender.pop(sender_id, None)
                self._refresh_sender_ready_candidate(sender_id)
                continue
            return request_id
        return None

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

    def compact_delivered_state(
        self,
        *,
        retain_global_sequences: int,
        retain_sender_locals: int,
    ) -> None:
        if retain_global_sequences < 0:
            return

        prune_upto_global = self.highest_contiguous_delivered_sequence - retain_global_sequences
        if prune_upto_global >= 0:
            stale_globals = [
                global_seq
                for global_seq in self.sequence_request_id_by_global
                if global_seq <= prune_upto_global
            ]
            for global_seq in stale_globals:
                request_id = self.sequence_request_id_by_global.pop(global_seq, None)
                self.sequence_message_cache.pop(global_seq, None)
                self.reset_sequence_retransmit(global_seq)
                if request_id is None:
                    continue
                if request_id in self.delivered_request_ids:
                    self._drop_request_state(request_id)

        if retain_sender_locals < 0:
            return

        for track in self.sender_tracks.values():
            if track.highest_request_local_contiguous >= 0:
                keep_after = track.highest_request_local_contiguous - retain_sender_locals
                if keep_after >= 0 and track.request_locals_seen:
                    track.request_locals_seen = {
                        value for value in track.request_locals_seen if value > keep_after
                    }
            if track.highest_assigned_local_contiguous >= 0:
                keep_after = track.highest_assigned_local_contiguous - retain_sender_locals
                if keep_after >= 0 and track.assigned_locals:
                    track.assigned_locals = {
                        value for value in track.assigned_locals if value > keep_after
                    }

    def schedule_sequence_retransmit(
        self,
        *,
        global_seq: int,
        now_monotonic: float,
        retry_interval_sec: float,
    ) -> bool:
        last = self.last_retransmit_sequence_request_at.get(global_seq, 0.0)
        interval = self.sequence_retransmit_retry_interval_sec.get(global_seq, retry_interval_sec)
        if now_monotonic - last < interval:
            return False
        self.last_retransmit_sequence_request_at[global_seq] = now_monotonic
        self.sequence_retransmit_retry_interval_sec[global_seq] = self._next_retry_interval(
            current=interval,
            base=retry_interval_sec,
        )
        return True

    def schedule_request_retransmit(
        self,
        *,
        sender_id: int,
        local_seq: int,
        now_monotonic: float,
        retry_interval_sec: float,
    ) -> bool:
        key = (sender_id, local_seq)
        last = self.last_retransmit_request_at.get(key, 0.0)
        interval = self.request_retransmit_retry_interval_sec.get(key, retry_interval_sec)
        if now_monotonic - last < interval:
            return False
        self.last_retransmit_request_at[key] = now_monotonic
        self.request_retransmit_retry_interval_sec[key] = self._next_retry_interval(
            current=interval,
            base=retry_interval_sec,
        )
        return True

    def reset_request_retransmit(self, sender_id: int, local_seq: int) -> None:
        key = (sender_id, local_seq)
        self.last_retransmit_request_at.pop(key, None)
        self.request_retransmit_retry_interval_sec.pop(key, None)

    def reset_sequence_retransmit(self, global_seq: int) -> None:
        self.last_retransmit_sequence_request_at.pop(global_seq, None)
        self.sequence_retransmit_retry_interval_sec.pop(global_seq, None)

    def _refresh_sender_ready_candidate(self, sender_id: int) -> None:
        expected_local = self.sender_tracks[sender_id].highest_assigned_local_contiguous + 1
        request_id = request_key(sender_id, expected_local)
        if (
            request_id in self.request_payload_by_id
            and request_id not in self.delivered_request_ids
            and request_id not in self.assigned_request_ids
        ):
            if self._ready_candidate_by_sender.get(sender_id) == request_id:
                return
            self._ready_candidate_by_sender[sender_id] = request_id
            heapq.heappush(
                self._ready_candidates_heap,
                (expected_local, sender_id, request_id),
            )
            return
        self._ready_candidate_by_sender.pop(sender_id, None)

    def _drop_request_state(self, request_id: str) -> None:
        self.request_payload_by_id.pop(request_id, None)
        self.assigned_request_ids.discard(request_id)
        self.delivered_request_ids.discard(request_id)
        sender_id, local_seq = parse_request_key(request_id)
        self.request_message_cache.pop((sender_id, local_seq), None)
        self.reset_request_retransmit(sender_id, local_seq)
        if self._ready_candidate_by_sender.get(sender_id) == request_id:
            self._ready_candidate_by_sender.pop(sender_id, None)

    def _next_retry_interval(
        self,
        *,
        current: float,
        base: float,
    ) -> float:
        backoff_multiplier = 1.4
        max_retry_sec = 2.0
        jitter_ratio = 0.15
        next_interval = max(base, current * backoff_multiplier)
        next_interval = min(max_retry_sec, next_interval)
        if jitter_ratio > 0.0:
            jitter = 1.0 + random.uniform(
                -jitter_ratio,
                jitter_ratio,
            )
            next_interval *= max(0.1, jitter)
        return max(base, min(max_retry_sec, next_interval))
