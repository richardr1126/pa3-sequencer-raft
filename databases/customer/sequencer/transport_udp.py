"""UDP transport for customer sequencer replication."""

import random
import socket
import threading
from typing import Callable

from .messages import WireMessage, decode_message

DEFAULT_SOCKET_TIMEOUT_SEC = 0.1
DEFAULT_SOCKET_RCVBUF_BYTES = 4 * 1024 * 1024
DEFAULT_SOCKET_SNDBUF_BYTES = 4 * 1024 * 1024


class UdpTransport:
    def __init__(
        self,
        *,
        bind_host: str,
        bind_port: int,
        drop_probability: float,
    ):
        self._bind_host = bind_host
        self._bind_port = bind_port
        self._socket_timeout_sec = DEFAULT_SOCKET_TIMEOUT_SEC
        self._socket_rcvbuf_bytes = DEFAULT_SOCKET_RCVBUF_BYTES
        self._socket_sndbuf_bytes = DEFAULT_SOCKET_SNDBUF_BYTES
        self._drop_probability = drop_probability

        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._running = False
        self._sock: socket.socket | None = None
        self._recv_thread: threading.Thread | None = None

    def start(self, on_message: Callable[[WireMessage], None]) -> None:
        with self._lock:
            if self._running:
                return
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(self._socket_timeout_sec)
            # Raise UDP socket buffers to reduce packet loss under bursty traffic.
            try:
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_RCVBUF, self._socket_rcvbuf_bytes
                )
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_SNDBUF, self._socket_sndbuf_bytes
                )
            except OSError:
                # Best effort only; continue with OS defaults if not permitted.
                pass
            sock.bind((self._bind_host, self._bind_port))
            self._sock = sock
            self._running = True

            thread = threading.Thread(
                target=self._recv_loop,
                args=(on_message,),
                name="customer-sequencer-recv",
                daemon=True,
            )
            self._recv_thread = thread
            thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            sock = self._sock
            self._sock = None
            recv_thread = self._recv_thread
            self._recv_thread = None

        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

        if recv_thread is not None:
            recv_thread.join(timeout=1.0)

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def sendto(self, payload: bytes, addr: tuple[str, int]) -> None:
        with self._lock:
            if not self._running:
                return
            sock = self._sock

        if sock is None:
            return
        if self._should_drop():
            return

        try:
            with self._send_lock:
                sock.sendto(payload, addr)
        except Exception:
            return

    def _recv_loop(self, on_message: Callable[[WireMessage], None]) -> None:
        while self.is_running():
            with self._lock:
                sock = self._sock
            if sock is None:
                return

            try:
                raw, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            except Exception:
                continue

            if self._should_drop():
                continue

            try:
                message = decode_message(raw)
            except Exception:
                continue

            try:
                on_message(message)
            except Exception:
                continue

    def _should_drop(self) -> bool:
        return self._drop_probability > 0 and random.random() < self._drop_probability
