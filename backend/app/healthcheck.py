"""Container health check: GET /api/health over the API's Unix socket (or TCP when HSA_BIND_TCP=1)."""
import http.client
import os
import socket
import sys


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=5)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(self.path)


def main():
    if os.environ.get("HSA_BIND_TCP") == "1":
        c = http.client.HTTPConnection("127.0.0.1", 8000, timeout=5)
    else:
        c = UnixHTTPConnection("/run/hsa/api.sock")
    try:
        c.request("GET", "/api/health")
        ok = c.getresponse().status == 200
    except OSError:
        ok = False
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
