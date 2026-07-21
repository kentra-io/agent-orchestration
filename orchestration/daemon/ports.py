"""Dashboard port allocation from the container's published range (design §6:
no reverse proxy — conductor binds CONDUCTOR_WEB_HOST inside the container and
the range is published verbatim, so container port == host port)."""

from __future__ import annotations

import socket


def parse_range(spec: str) -> tuple[int, int]:
    lo, _, hi = spec.partition("-")
    return int(lo), int(hi)


class PortAllocator:
    def __init__(self, low: int, high: int) -> None:
        self._low, self._high = low, high
        self._handed_out: set[int] = set()

    def allocate(self) -> int:
        for port in range(self._low, self._high + 1):
            if port in self._handed_out:
                continue
            try:
                with socket.socket() as s:
                    s.bind(("0.0.0.0", port))
            except OSError:
                continue
            self._handed_out.add(port)
            return port
        raise RuntimeError(f"no free dashboard port in {self._low}-{self._high}")
