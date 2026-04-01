"""Retransmit/NACK helpers for missing request/sequence detection."""

from __future__ import annotations

from .messages import RetransmitMessage, parse_request_key
from .state_store import SequencerState


def collect_missing_sequence_retransmits(
    *,
    state: SequencerState,
    member_count: int,
    retry_interval_sec: float,
    now_monotonic: float,
    self_id: int,
) -> list[tuple[int, RetransmitMessage]]:
    retransmits: list[tuple[int, RetransmitMessage]] = []

    def maybe_add(global_seq: int) -> None:
        last = state.last_retransmit_sequence_request_at.get(global_seq, 0.0)
        if now_monotonic - last < retry_interval_sec:
            return

        state.last_retransmit_sequence_request_at[global_seq] = now_monotonic
        target_id = global_seq % member_count
        retransmits.append(
            (
                target_id,
                RetransmitMessage(
                    sender_id=self_id,
                    target_id=target_id,
                    mode="sequence",
                    global_seq=global_seq,
                    recv_upto=state.local_receive_upto(),
                ),
            )
        )

    next_expected = state.highest_contiguous_sequence + 1
    if next_expected not in state.sequence_request_id_by_global:
        maybe_add(next_expected)

    for seq in range(state.highest_contiguous_sequence + 1, state.highest_seen_sequence + 1):
        if seq in state.sequence_request_id_by_global:
            continue
        maybe_add(seq)

    return retransmits


def collect_missing_request_retransmits(
    *,
    state: SequencerState,
    retry_interval_sec: float,
    now_monotonic: float,
    self_id: int,
) -> list[tuple[int, RetransmitMessage]]:
    retransmits: list[tuple[int, RetransmitMessage]] = []

    def maybe_add(sender_id: int, local_seq: int) -> None:
        key = (sender_id, local_seq)
        last = state.last_retransmit_request_at.get(key, 0.0)
        if now_monotonic - last < retry_interval_sec:
            return

        state.last_retransmit_request_at[key] = now_monotonic
        retransmits.append(
            (
                sender_id,
                RetransmitMessage(
                    sender_id=self_id,
                    target_id=sender_id,
                    mode="request",
                    request_sender_id=sender_id,
                    request_local_seq=local_seq,
                    recv_upto=state.local_receive_upto(),
                ),
            )
        )

    for sender_id in state.members:
        track = state.sender_tracks[sender_id]
        highest_contiguous_local = track.highest_request_local_contiguous
        highest_seen_local = track.highest_request_local_seen
        if highest_seen_local <= highest_contiguous_local:
            continue

        seen_locals = track.request_locals_seen
        for local_seq in range(highest_contiguous_local + 1, highest_seen_local + 1):
            if local_seq in seen_locals:
                continue
            maybe_add(sender_id, local_seq)

    # Also retry requests we know must exist because a Sequence already references them.
    for request_id in state.sequence_request_id_by_global.values():
        if request_id in state.request_payload_by_id:
            continue
        sender_id, local_seq = parse_request_key(request_id)
        maybe_add(sender_id, local_seq)

    return retransmits
