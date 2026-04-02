"""Rotating sequencer replication engine (UDP transport + protocol orchestration)."""

import logging
import threading
import time
from typing import Any, Callable

from .config import CustomerSequencerConfig
from .messages import (
    RequestMessage,
    RetransmitMessage,
    SequenceMessage,
    WireMessage,
    encode_message,
    request_key,
)
from .retransmit import (
    collect_missing_request_retransmits,
    collect_missing_sequence_retransmits,
)
from .sequencer import assign_sequence_messages
from .state_store import PendingCommand, SequencerState
from .transport_udp import UdpTransport


class CustomerSequencerError(Exception):
    pass


class CustomerSequencerEngine:
    """Rotating-sequencer atomic broadcast with UDP + NACK retransmit."""

    def __init__(
        self,
        *,
        config: CustomerSequencerConfig,
        apply_fn: Callable[[str, dict[str, Any]], dict[str, Any]],
        logger: logging.Logger | None = None,
    ):
        self.config = config
        self._apply_fn = apply_fn
        self._log = logger or logging.getLogger(__name__)

        self._lock = threading.RLock()
        self._running = False
        self._threads: list[threading.Thread] = []

        self._state = SequencerState(members=config.members, self_id=config.self_id)
        self._member_count = len(config.members)
        self._member_addrs: dict[int, tuple[str, int]] = {
            member_id: self._parse_addr(addr)
            for member_id, addr in self.config.members.items()
        }
        self._transport = UdpTransport(
            bind_host=config.udp_bind_host,
            bind_port=config.udp_bind_port,
            drop_probability=config.drop_probability,
        )
        self._logged_missing_requests: set[tuple[int, int]] = set()
        self._logged_missing_sequences: set[int] = set()
        self._last_progress_broadcast_upto = -1

    @staticmethod
    def _parse_addr(value: str) -> tuple[str, int]:
        host, port_raw = value.rsplit(":", 1)
        return host, int(port_raw)

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._state.peer_receive_upto_by_member[self.config.self_id] = (
                self._state.local_receive_upto()
            )

        self._transport.start(self._on_wire_message)
        self._threads = [
            threading.Thread(
                target=self._maintenance_loop,
                name="customer-sequencer-maintenance",
                daemon=True,
            ),
        ]
        for thread in self._threads:
            thread.start()
        self._log.info(
            "customer-sequencer started: node=%d bind=%s:%d members=%d retry_sec=%.3f timeout_sec=%.1f drop_prob=%.2f",
            self.config.self_id,
            self.config.udp_bind_host,
            self.config.udp_bind_port,
            len(self.config.members),
            self.config.retransmit_retry_sec,
            self.config.command_timeout_sec,
            self.config.drop_probability,
        )

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False

        self._transport.stop()
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._threads.clear()
        self._log.info("customer-sequencer stopped: node=%d", self.config.self_id)

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def submit(self, method: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if not self._running:
                raise CustomerSequencerError("Sequencer engine is not running")

            local_seq = self._state.allocate_local_request_seq()
            sender_id = self.config.self_id
            req_id = request_key(sender_id, local_seq)

            pending = PendingCommand()
            self._state.pending_local_commands[req_id] = pending

            request_message = RequestMessage(
                sender_id=sender_id,
                request_sender_id=sender_id,
                request_local_seq=local_seq,
                method=method,
                kwargs=kwargs,
                recv_upto=self._state.local_receive_upto(),
            )
            encoded_request = encode_message(request_message)

            # Cache encoded payloads for exact retransmit replies.
            self._state.request_message_cache[(sender_id, local_seq)] = encoded_request
            _, missing_locals = self._state.register_request(
                request_sender_id=sender_id,
                request_local_seq=local_seq,
                method=method,
                kwargs=kwargs,
            )
            now = time.monotonic()
            for missing_local in missing_locals:
                self._send_retransmit_request_locked(
                    target_sender_id=sender_id,
                    request_sender_id=sender_id,
                    request_local_seq=missing_local,
                    now=now,
                )

        self._broadcast_encoded(encoded_request)

        deadline = time.monotonic() + self.config.command_timeout_sec
        next_rebroadcast_at = time.monotonic() + self.config.retransmit_retry_sec
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                with self._lock:
                    self._state.pending_local_commands.pop(req_id, None)
                    assigned_upto = self._state.highest_contiguous_sequence
                    received_upto = self._state.highest_contiguous_received_sequence
                    delivered_upto = self._state.highest_contiguous_delivered_sequence
                    pending_local = len(self._state.pending_local_commands)
                self._log.error(
                    "customer-sequencer timeout: req_id=%s method=%s wait_sec=%.2f assigned_upto=%d received_upto=%d delivered_upto=%d pending_local=%d",
                    req_id,
                    method,
                    self.config.command_timeout_sec,
                    assigned_upto,
                    received_upto,
                    delivered_upto,
                    pending_local,
                )
                raise CustomerSequencerError(
                    f"Timed out waiting for replicated command {method} ({req_id})"
                )

            if pending.event.wait(timeout=min(remaining, 0.1)):
                break

            if now >= next_rebroadcast_at:
                # Rebroadcast early to speed recovery under packet loss.
                self._broadcast_encoded(encoded_request)
                next_rebroadcast_at = now + self.config.retransmit_retry_sec

        with self._lock:
            self._state.pending_local_commands.pop(req_id, None)
        if pending.error is not None:
            raise pending.error
        return pending.result

    def _on_wire_message(self, message: WireMessage) -> None:
        progress_messages: list[tuple[int, RetransmitMessage]] = []
        with self._lock:
            if not self._running:
                return
            previous_local_recv_upto = self._state.local_receive_upto()
            sender_recv_upto = getattr(message, "recv_upto", None)
            self._handle_wire_message_locked(message)
            progress_messages = self._collect_progress_messages_locked(
                previous_local_recv_upto=previous_local_recv_upto,
                sender_id=int(message.sender_id),
                sender_recv_upto=(
                    int(sender_recv_upto) if isinstance(sender_recv_upto, int) else None
                ),
            )
        for target_id, progress_message in progress_messages:
            self._send_message_to_member(target_id, progress_message)

    def _handle_wire_message_locked(self, message: WireMessage) -> None:
        sender_id = int(message.sender_id)
        if sender_id not in self.config.members:
            return

        recv_upto = getattr(message, "recv_upto", None)
        if isinstance(recv_upto, int):
            self._state.peer_receive_upto_by_member[sender_id] = max(
                self._state.peer_receive_upto_by_member[sender_id],
                recv_upto,
            )

        if isinstance(message, RequestMessage):
            self._on_request_locked(message)
            return
        if isinstance(message, SequenceMessage):
            self._on_sequence_locked(message)
            return
        if isinstance(message, RetransmitMessage):
            self._on_retransmit_locked(message)
            return

    def _on_request_locked(self, message: RequestMessage) -> None:
        req_sender_id = int(message.request_sender_id)
        req_local_seq = int(message.request_local_seq)
        if req_sender_id not in self.config.members or req_local_seq < 0:
            return

        _, missing_locals = self._state.register_request(
            request_sender_id=req_sender_id,
            request_local_seq=req_local_seq,
            method=message.method,
            kwargs=dict(message.kwargs),
        )

        now = time.monotonic()
        for missing_local in missing_locals:
            key = (req_sender_id, missing_local)
            if key not in self._logged_missing_requests:
                self._logged_missing_requests.add(key)
                self._log.warning(
                    "customer-sequencer gap detected: type=request sender=%d local_seq=%d detected_by=request",
                    req_sender_id,
                    missing_local,
                )
            self._send_retransmit_request_locked(
                target_sender_id=req_sender_id,
                request_sender_id=req_sender_id,
                request_local_seq=missing_local,
                now=now,
            )
        key = (req_sender_id, req_local_seq)
        if key in self._logged_missing_requests:
            self._logged_missing_requests.remove(key)
            self._log.info(
                "customer-sequencer gap recovered: type=request sender=%d local_seq=%d",
                req_sender_id,
                req_local_seq,
            )

    def _on_sequence_locked(self, message: SequenceMessage) -> None:
        global_seq = int(message.global_seq)
        req_sender_id = int(message.request_sender_id)
        req_local_seq = int(message.request_local_seq)
        sender_id = int(message.sender_id)

        if global_seq < 0:
            return
        if req_sender_id not in self.config.members or req_local_seq < 0:
            return
        if sender_id not in self.config.members:
            return
        if sender_id != global_seq % self._member_count:
            # Reject if sender is not the owner for this global sequence.
            return

        req_id = request_key(req_sender_id, req_local_seq)
        previous_highest_contiguous = self._state.highest_contiguous_sequence
        accepted = self._state.register_sequence(global_seq=global_seq, request_id=req_id)
        if not accepted:
            return

        if req_id not in self._state.request_payload_by_id:
            # Request can be missing when sequence arrives due to UDP reordering/loss.
            key = (req_sender_id, req_local_seq)
            if key not in self._logged_missing_requests:
                self._logged_missing_requests.add(key)
                self._log.warning(
                    "customer-sequencer gap detected: type=request sender=%d local_seq=%d detected_by=sequence",
                    req_sender_id,
                    req_local_seq,
                )
            self._send_retransmit_request_locked(
                target_sender_id=req_sender_id,
                request_sender_id=req_sender_id,
                request_local_seq=req_local_seq,
                now=time.monotonic(),
            )

        if global_seq > previous_highest_contiguous + 1:
            now = time.monotonic()
            for missing_seq in range(previous_highest_contiguous + 1, global_seq):
                if missing_seq in self._state.sequence_request_id_by_global:
                    continue
                if missing_seq not in self._logged_missing_sequences:
                    self._logged_missing_sequences.add(missing_seq)
                    self._log.warning(
                        "customer-sequencer gap detected: type=sequence global_seq=%d",
                        missing_seq,
                    )
                self._send_retransmit_sequence_locked(
                    target_sender_id=missing_seq % self._member_count,
                    global_seq=missing_seq,
                    now=now,
                )
        if global_seq in self._logged_missing_sequences:
            self._logged_missing_sequences.remove(global_seq)
            self._log.info(
                "customer-sequencer gap recovered: type=sequence global_seq=%d",
                global_seq,
            )

    def _on_retransmit_locked(self, message: RetransmitMessage) -> None:
        if message.target_id is not None and int(message.target_id) != self.config.self_id:
            return

        requester_id = int(message.sender_id)
        if requester_id not in self.config.members:
            return

        if message.mode == "progress":
            return

        if message.mode == "request":
            req_sender_id = message.request_sender_id
            req_local_seq = message.request_local_seq
            if req_sender_id is None or req_local_seq is None:
                return
            payload = self._state.request_message_cache.get(
                (int(req_sender_id), int(req_local_seq))
            )
            if payload is None:
                return
            self._log.debug(
                "customer-sequencer retransmit served: type=request to=%d sender=%d local_seq=%d",
                requester_id,
                int(req_sender_id),
                int(req_local_seq),
            )
            self._send_encoded_to_member(requester_id, payload)
            return

        if message.mode == "sequence":
            global_seq = message.global_seq
            if global_seq is None:
                return
            payload = self._state.sequence_message_cache.get(int(global_seq))
            if payload is None:
                return
            self._log.debug(
                "customer-sequencer retransmit served: type=sequence to=%d global_seq=%d",
                requester_id,
                int(global_seq),
            )
            self._send_encoded_to_member(requester_id, payload)
            return

    def _collect_progress_messages_locked(
        self,
        *,
        previous_local_recv_upto: int,
        sender_id: int | None = None,
        sender_recv_upto: int | None = None,
    ) -> list[tuple[int, RetransmitMessage]]:
        current_recv_upto = self._state.local_receive_upto()
        messages: list[tuple[int, RetransmitMessage]] = []
        sent_targets: set[int] = set()

        # Broadcast immediate progress when recv_upto advances.
        if (
            current_recv_upto > previous_local_recv_upto
            and current_recv_upto > self._last_progress_broadcast_upto
        ):
            self._last_progress_broadcast_upto = current_recv_upto
            for peer_id in self.config.members:
                if peer_id == self.config.self_id:
                    continue
                messages.append(
                    (
                        peer_id,
                        RetransmitMessage(
                            sender_id=self.config.self_id,
                            target_id=peer_id,
                            mode="progress",
                            recv_upto=current_recv_upto,
                        ),
                    )
                )
                sent_targets.add(peer_id)

        # If sender appears behind our recv_upto, send targeted progress hint back.
        if (
            sender_id is not None
            and sender_id != self.config.self_id
            and sender_id in self.config.members
            and isinstance(sender_recv_upto, int)
            and current_recv_upto > sender_recv_upto
            and sender_id not in sent_targets
        ):
            messages.append(
                (
                    sender_id,
                    RetransmitMessage(
                        sender_id=self.config.self_id,
                        target_id=sender_id,
                        mode="progress",
                        recv_upto=current_recv_upto,
                    ),
                )
            )

        return messages

    def _maintenance_loop(self) -> None:
        while self.is_running():
            with self._lock:
                now = time.monotonic()
                previous_local_recv_upto = self._state.local_receive_upto()
                self._state.peer_receive_upto_by_member[self.config.self_id] = (
                    self._state.local_receive_upto()
                )

                sequence_messages = assign_sequence_messages(
                    state=self._state,
                    self_id=self.config.self_id,
                    member_count=self._member_count,
                )
                # Phase 1: assign and broadcast sequence numbers this node owns.
                sequence_payloads: list[bytes] = []
                for seq_msg in sequence_messages:
                    encoded = encode_message(seq_msg)
                    self._state.sequence_message_cache[int(seq_msg.global_seq)] = encoded
                    sequence_payloads.append(encoded)

                # Phase 2: detect gaps and send retransmit requests.
                retransmit_messages = collect_missing_sequence_retransmits(
                    state=self._state,
                    member_count=self._member_count,
                    retry_interval_sec=self.config.retransmit_retry_sec,
                    now_monotonic=now,
                    self_id=self.config.self_id,
                )
                retransmit_messages.extend(
                    collect_missing_request_retransmits(
                        state=self._state,
                        retry_interval_sec=self.config.retransmit_retry_sec,
                        now_monotonic=now,
                        self_id=self.config.self_id,
                    )
                )

                deliverable = self._state.next_deliverable(
                    majority=self.config.majority,
                )
                progress_messages = self._collect_progress_messages_locked(
                    previous_local_recv_upto=previous_local_recv_upto,
                )

            for payload in sequence_payloads:
                self._broadcast_encoded(payload)

            for target_id, retransmit_message in retransmit_messages:
                self._send_message_to_member(target_id, retransmit_message)

            for target_id, progress_message in progress_messages:
                self._send_message_to_member(target_id, progress_message)

            if deliverable is None:
                time.sleep(0.01)
                continue

            # Phase 4: apply one next-in-order deliverable command.
            global_seq, req_id, payload = deliverable
            method = str(payload["method"])
            kwargs = dict(payload["kwargs"])

            result: dict[str, Any] | None = None
            error: Exception | None = None
            started_at = time.monotonic()
            try:
                result = self._apply_fn(method, kwargs)
            except Exception as exc:
                error = exc
            duration_ms = (time.monotonic() - started_at) * 1000.0
            if duration_ms >= 100.0:
                self._log.warning(
                    "customer-sequencer slow apply: global_seq=%d req_id=%s method=%s duration_ms=%.1f",
                    global_seq,
                    req_id,
                    method,
                    duration_ms,
                )

            with self._lock:
                self._state.mark_delivered(
                    global_seq=global_seq,
                    request_id=req_id,
                    result=result,
                    error=error,
                )

    def _send_retransmit_request_locked(
        self,
        *,
        target_sender_id: int,
        request_sender_id: int,
        request_local_seq: int,
        now: float,
    ) -> None:
        key = (request_sender_id, request_local_seq)
        last = self._state.last_retransmit_request_at.get(key, 0.0)
        if now - last < self.config.retransmit_retry_sec:
            return

        self._state.last_retransmit_request_at[key] = now
        self._send_message_to_member(
            target_sender_id,
            RetransmitMessage(
                sender_id=self.config.self_id,
                target_id=target_sender_id,
                mode="request",
                request_sender_id=request_sender_id,
                request_local_seq=request_local_seq,
                recv_upto=self._state.local_receive_upto(),
            ),
        )

    def _send_retransmit_sequence_locked(
        self,
        *,
        target_sender_id: int,
        global_seq: int,
        now: float,
    ) -> None:
        last = self._state.last_retransmit_sequence_request_at.get(global_seq, 0.0)
        if now - last < self.config.retransmit_retry_sec:
            return

        self._state.last_retransmit_sequence_request_at[global_seq] = now
        self._send_message_to_member(
            target_sender_id,
            RetransmitMessage(
                sender_id=self.config.self_id,
                target_id=target_sender_id,
                mode="sequence",
                global_seq=global_seq,
                recv_upto=self._state.local_receive_upto(),
            ),
        )

    def _send_message_to_member(self, member_id: int, message: RequestMessage | SequenceMessage | RetransmitMessage) -> None:
        try:
            payload = encode_message(message)
        except Exception:
            return
        self._send_encoded_to_member(member_id, payload)

    def _broadcast_encoded(self, payload: bytes) -> None:
        for member_id in self.config.members:
            if member_id == self.config.self_id:
                continue
            self._send_encoded_to_member(member_id, payload)

    def _send_encoded_to_member(self, member_id: int, payload: bytes) -> None:
        if member_id == self.config.self_id:
            return
        with self._lock:
            if not self._running:
                return
        addr = self._member_addrs.get(member_id)
        if addr is None:
            return
        self._transport.sendto(payload, addr)
