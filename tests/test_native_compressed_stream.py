import gzip
import io
import json
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

import pytest

from services.native_bridge import CompressedStateStream


def test_small_compressed_frame_is_read_before_server_waiting_for_ack_closes():
    acknowledged = threading.Event()
    payload = {"op": "changes", "base": 10, "sequence": 11}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            compressor = zlib.compressobj(wbits=31)
            self.wfile.write(compressor.compress(json.dumps(payload).encode()+b"\n")+
                             compressor.flush(zlib.Z_SYNC_FLUSH))
            self.wfile.flush()
            acknowledged.wait(3)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}", timeout=1) as response:
            stream = CompressedStateStream(response)
            assert json.loads(stream.readline(1024)) == payload
            acknowledged.set()
    finally:
        acknowledged.set()
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_compressed_input_preserves_large_integer_and_negative_financial_values():
    row = {"Ticket":"18446744073709551615", "Equity": -12345.67}
    data = gzip.compress(json.dumps(row).encode()+b"\n")
    stream = CompressedStateStream(io.BytesIO(data))
    assert json.loads(stream.readline()) == row


def test_inflated_frame_limit_is_enforced_before_unbounded_allocation():
    data = gzip.compress(b"x" * 1000000 + b"\n")
    stream = CompressedStateStream(io.BytesIO(data))
    assert stream.readline(1025) == b"x" * 1025
    assert len(stream.pending) == 0


def test_truncated_compressed_frame_is_rejected():
    stream = CompressedStateStream(io.BytesIO(gzip.compress(b'{"partial":')[:-8]))
    with pytest.raises(EOFError):
        stream.readline()
