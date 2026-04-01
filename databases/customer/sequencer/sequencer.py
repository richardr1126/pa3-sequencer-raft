"""Sequencer assignment helpers for rotating ownership."""

from __future__ import annotations

from .messages import SequenceMessage, parse_request_key
from .state_store import SequencerState


def assign_sequence_messages(
    *,
    state: SequencerState,
    self_id: int,
    member_count: int,
) -> list[SequenceMessage]:
    messages: list[SequenceMessage] = []

    while True:
        next_global = state.highest_contiguous_sequence + 1

        if next_global in state.sequence_request_id_by_global:
            state.highest_contiguous_sequence = next_global
            continue

        if next_global % member_count != self_id:
            return messages

        if state.highest_contiguous_received_sequence < next_global - 1:
            return messages

        request_id = state.pick_sequence_candidate_request_id()
        if request_id is None:
            return messages

        request_sender_id, request_local_seq = parse_request_key(request_id)
        message = SequenceMessage(
            sender_id=self_id,
            global_seq=next_global,
            request_sender_id=request_sender_id,
            request_local_seq=request_local_seq,
            recv_upto=state.local_receive_upto(),
        )

        if not state.register_sequence(global_seq=next_global, request_id=request_id):
            continue
        messages.append(message)
