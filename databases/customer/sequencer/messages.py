"""Typed wire messages for customer sequencer replication."""

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, model_validator

MAX_UDP_PAYLOAD_BYTES = 64 * 1024


class MessageError(Exception):
    pass


def request_key(sender_id: int, local_seq: int) -> str:
    return f"{sender_id}:{local_seq}"


def parse_request_key(value: str) -> tuple[int, int]:
    if ":" not in value:
        raise MessageError(f"invalid request key: {value!r}")
    sender_raw, local_raw = value.split(":", 1)
    return int(sender_raw), int(local_raw)


class RequestMessage(BaseModel):
    type: Literal["request"] = "request"
    sender_id: int
    request_sender_id: int
    request_local_seq: int
    method: str
    kwargs: dict[str, Any]
    recv_upto: int | None = None


class SequenceMessage(BaseModel):
    type: Literal["sequence"] = "sequence"
    sender_id: int
    global_seq: int
    request_sender_id: int
    request_local_seq: int
    recv_upto: int | None = None


class RetransmitMessage(BaseModel):
    type: Literal["retransmit"] = "retransmit"
    sender_id: int
    target_id: int | None = None
    mode: Literal["progress", "request", "sequence"]
    request_sender_id: int | None = None
    request_local_seq: int | None = None
    global_seq: int | None = None
    recv_upto: int | None = None

    @model_validator(mode="after")
    def _validate_mode_fields(self) -> "RetransmitMessage":
        if self.mode == "request":
            if self.request_sender_id is None or self.request_local_seq is None:
                raise ValueError("request mode requires request_sender_id and request_local_seq")
        if self.mode == "sequence":
            if self.global_seq is None:
                raise ValueError("sequence mode requires global_seq")
        return self


WireMessage = Annotated[
    RequestMessage | SequenceMessage | RetransmitMessage,
    Field(discriminator="type"),
]
WIRE_ADAPTER = TypeAdapter(WireMessage)


def encode_message(message: BaseModel | dict[str, Any]) -> bytes:
    payload_dict = (
        message.model_dump(exclude_none=True)
        if isinstance(message, BaseModel)
        else message
    )
    payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(payload) > MAX_UDP_PAYLOAD_BYTES:
        raise MessageError("message exceeds UDP payload limit")
    return payload


def decode_message(raw: bytes) -> WireMessage:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise MessageError(f"invalid message payload: {exc}") from exc
    try:
        return WIRE_ADAPTER.validate_python(payload)
    except Exception as exc:
        raise MessageError(f"invalid wire message: {exc}") from exc
