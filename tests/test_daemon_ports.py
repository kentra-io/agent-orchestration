import socket

from orchestration.daemon.ports import PortAllocator, parse_range


def test_parse_range():
    assert parse_range("42000-42050") == (42000, 42050)


def test_allocates_free_port_and_skips_reserved():
    alloc = PortAllocator(42000, 42010)
    p1 = alloc.allocate()
    p2 = alloc.allocate()
    assert p1 != p2 and 42000 <= p1 <= 42010


def test_skips_ports_bound_by_others():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        used = s.getsockname()[1]
        alloc = PortAllocator(used, used + 3)
        assert alloc.allocate() != used
