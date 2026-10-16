"""Production entry point for the API server.

    python -m autoforge.serve            # listens on $PORT (default 8000)

Binds one dual-stack socket ("::" with IPV6_V6ONLY off) so the API accepts IPv4 and IPv6 connections, which
private networks such as Railway's require (``uvicorn --host ::`` alone is IPv6-only). Falls back to IPv4 on
hosts without IPv6.
"""

from __future__ import annotations

import os
import socket

import uvicorn


def listen_socket(port: int) -> socket.socket:
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("::", port))
        except OSError:
            sock.close()
            raise
    except OSError:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
    sock.listen(2048)
    return sock


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    config = uvicorn.Config("autoforge.api.main:create_app", factory=True, proxy_headers=True,
                            forwarded_allow_ips="*", server_header=False)
    uvicorn.Server(config).run(sockets=[listen_socket(port)])


if __name__ == "__main__":
    main()
