import io

import pytest

from viewer.relay import FrameRelay, StreamError


def _part(jpeg: bytes) -> bytes:
    return b"--frameboundary\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n%s\r\n" % (len(jpeg), jpeg)


def test_reads_each_frame_and_keeps_the_newest():
    relay = FrameRelay("http://device/stream", timeout_s=1)
    with pytest.raises(StreamError, match="closed"):
        relay._read(io.BytesIO(_part(b"first") + _part(b"\xff\xd8second\r\n--x")))
    sequence, jpeg = relay.wait(after=0, timeout=0)
    assert (sequence, jpeg) == (2, b"\xff\xd8second\r\n--x")


def test_a_truncated_frame_is_an_error():
    relay = FrameRelay("http://device/stream", timeout_s=1)
    with pytest.raises(StreamError, match="inside a frame"):
        relay._read(io.BytesIO(b"--frameboundary\r\nContent-Length: 10\r\n\r\nshort"))
    assert relay.wait(after=0, timeout=0) == (0, None)
