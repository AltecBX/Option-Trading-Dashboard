"""JERRY_NO_NET, enforced in one place.

`JERRY_NO_NET=1` has always meant "this process makes no outbound
request". Until now each module promised that for itself — `edge_scan`,
`gap_scan`, `recovery`, `ewhispers` and a dozen others check the flag
before they fetch — and any path that forgot simply went to the
internet. `/api/ticker` was one: the render suite starts the server with
the flag set, and every chart it drew was quietly fetched from Yahoo.
When Yahoo rate-limited the machine, the page grew a "slow down" banner,
content moved down 100px, and layout tests failed on code nobody had
touched.

`install()` makes the flag a property of the process instead of a
promise of each module. It closes both ways out:

* Python's own sockets — `urllib`, `requests`, `http.client` and anything
  else built on the `socket` module. A connection to anything but this
  machine is refused.
* curl_cffi, which yfinance uses and which does its networking in C, so a
  socket guard never sees it. Its session `request` methods refuse before
  a handle is opened.

Loopback stays open: the dashboard is itself a local server, and tests
talk to it. A proxy is not, even on loopback: a sandbox's egress proxy
usually listens on 127.0.0.1, and `urllib`/`requests` route every request
through whatever `HTTPS_PROXY` names — so the address a proxy variable
names is refused like any remote host.

The refusal is a `ConnectionError`, which is an `OSError` — the same
thing a dropped network raises — so every caller's existing offline
handling runs, and nothing is fabricated in its place.
"""

from __future__ import annotations

import ipaddress
import os
import socket

ENV = "JERRY_NO_NET"
_INSTALLED = False


class NetworkDisabled(ConnectionError):
    """An outbound request refused because JERRY_NO_NET is set."""


def enabled() -> bool:
    """Whether the flag asks for no network. Any non-empty value other than
    "0" counts: modules have read it both as `== "1"` and as truthy, and
    the stricter reading is the one that cannot leak."""
    v = os.environ.get(ENV, "")
    return bool(v) and v != "0"


def _is_local(host) -> bool:
    if host is None:
        return False
    h = str(host).strip("[]").lower()
    if h in ("localhost", "") or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h.split("%")[0]).is_loopback
    except ValueError:
        return False


_PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
               "ALL_PROXY", "all_proxy")


def _proxy_endpoints() -> set:
    """(host, port) of every proxy the environment names. Read at connect
    time, not install time, so a proxy set later is still a way out."""
    from urllib.parse import urlsplit
    out = set()
    for var in _PROXY_VARS:
        v = os.environ.get(var)
        if not v:
            continue
        try:
            u = urlsplit(v if "://" in v else "http://" + v)
            default = 443 if u.scheme == "https" else 1080 if u.scheme.startswith("socks") else 80
            if u.hostname:
                out.add((u.hostname.lower(), u.port or default))
        except ValueError:
            continue
    return out


def _allowed(address) -> bool:
    """A socket address this process may still connect to: this machine,
    unless it is where a proxy listens."""
    if isinstance(address, (str, bytes)):      # AF_UNIX: a path, local
        return True
    if not (isinstance(address, tuple) and address):
        return False
    host, port = address[0], (address[1] if len(address) > 1 else None)
    if not _is_local(host):
        return False
    for ph, pp in _proxy_endpoints():
        if pp == port and (_is_local(ph) or ph == str(host).lower()):
            return False
    return True


def _refuse(what) -> None:
    raise NetworkDisabled(f"{ENV} is set — outbound connection to {what} refused")


def install() -> bool:
    """Close the process's ways out. Idempotent; returns whether the guard
    is (now) in place. A no-op when the flag is not set."""
    global _INSTALLED
    if _INSTALLED:
        return True
    if not enabled():
        return False

    # ── Python sockets ──────────────────────────────────────────────────
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self, address):
        if not _allowed(address):
            _refuse(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        if not _allowed(address):
            _refuse(address)
        return real_connect_ex(self, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex

    # ── curl_cffi (yfinance) ────────────────────────────────────────────
    try:
        from urllib.parse import urlparse
        from curl_cffi import requests as _cr

        def _guard(real):
            def request(self, method, url, *a, **kw):
                host = urlparse(str(url)).hostname
                if not _is_local(host):
                    _refuse(host)
                return real(self, method, url, *a, **kw)
            request.__wrapped__ = real
            return request

        for name in ("Session", "AsyncSession"):
            cls = getattr(_cr, name, None)
            if cls is not None and hasattr(cls, "request"):
                cls.request = _guard(cls.request)
    except ImportError:
        pass

    _INSTALLED = True
    return True
