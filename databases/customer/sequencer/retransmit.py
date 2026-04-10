"""Retransmit/NACK helpers for missing request/sequence detection."""

from .config import CustomerSequencerConfig
from .messages import RetransmitMessage, parse_request_key
from .state_store import SequencerState


def collect_missing_sequence_retransmits(
    *,
    state: SequencerState,
    member_count: int,
    config: CustomerSequencerConfig,
    now_monotonic: float,
    self_id: int,
) -> list[tuple[int, RetransmitMessage]]:
    retransmits: list[tuple[int, RetransmitMessage]] = []

    def maybe_add(global_seq: int) -> None:
        if not state.schedule_sequence_retransmit(
            global_seq=global_seq,
            now_monotonic=now_monotonic,
            retry_interval_sec=config.retransmit_retry_sec,
        ):
            return

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

    upper_bound = min(
        state.highest_seen_sequence,
        state.highest_contiguous_sequence + max(config.retransmit_scan_window, 1),
    )
    for seq in range(state.highest_contiguous_sequence + 1, upper_bound + 1):
        if seq in state.sequence_request_id_by_global:
            continue
        maybe_add(seq)

    return retransmits


def collect_missing_request_retransmits(
    *,
    state: SequencerState,
    config: CustomerSequencerConfig,
    now_monotonic: float,
    self_id: int,
) -> list[tuple[int, RetransmitMessage]]:
    retransmits: list[tuple[int, RetransmitMessage]] = []

    def maybe_add(sender_id: int, local_seq: int) -> None:
        if not state.schedule_request_retransmit(
            sender_id=sender_id,
            local_seq=local_seq,
            now_monotonic=now_monotonic,
            retry_interval_sec=config.retransmit_retry_sec,
        ):
            return

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
        upper_bound = min(
            highest_seen_local,
            highest_contiguous_local + max(config.retransmit_scan_window, 1),
        )
        for local_seq in range(highest_contiguous_local + 1, upper_bound + 1):
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
