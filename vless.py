"""VLESS WebSocket + TLS link and request-header helpers."""
import ipaddress
import os
import time
from urllib.parse import quote, urlsplit

_link_cache = {}
_CACHE_TTL = 300


def get_domain() -> str:
    raw = (os.environ.get("DOMAIN") or os.environ.get("RAILWAY_PUBLIC_DOMAIN")
           or os.environ.get("RENDER_EXTERNAL_URL") or "localhost").strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlsplit(raw)
    host = parsed.hostname or "localhost"
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        host = host.lower().rstrip(".")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{parsed.port}" if parsed.port else host


def _split_endpoint(endpoint: str, default_port: int = 443):
    raw = (endpoint or "").strip()
    if not raw:
        return "localhost", default_port
    if "://" in raw:
        raw = urlsplit(raw).netloc or raw
    raw = raw.strip().rstrip("/")
    if raw.startswith("[") and "]" in raw:
        end = raw.find("]")
        host, rest = raw[1:end], raw[end + 1:]
        port = int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else default_port
        return host, port
    if raw.count(":") == 1:
        host, port = raw.rsplit(":", 1)
        if port.isdigit():
            return host, int(port)
    return raw, default_port


def _host_port(host: str, port: int) -> str:
    try:
        ipaddress.IPv6Address(host)
        return f"[{host}]:{port}"
    except ipaddress.AddressValueError:
        return f"{host}:{port}"


def build_vless_link(uid: str, remark: str = "VLESS", address: str | None = None) -> str:
    endpoint = address or get_domain()
    host, port = _split_endpoint(endpoint)
    try:
        is_ip = bool(ipaddress.ip_address(host))
    except ValueError:
        is_ip = False
    domain = get_domain()
    sni = domain if is_ip else host
    path = f"/ws/{uid}"
    params = {
        "encryption": "none", "security": "tls", "type": "ws",
        "host": sni, "path": path, "sni": sni, "fp": "chrome", "alpn": "http/1.1",
    }
    query = "&".join(f"{key}={quote(str(value), safe='')}" for key, value in params.items())
    link = f"vless://{uid}@{_host_port(host, port)}?{query}#{quote(remark, safe='')}"
    cache_key = f"{uid}:{remark}:{endpoint}"
    cached = _link_cache.get(cache_key)
    now = time.time()
    if cached and cached[0] > now:
        return cached[1]
    _link_cache[cache_key] = (now + _CACHE_TTL, link)
    return link


def parse_vless_header(data: bytes):
    """Return version, command, destination host, port, and payload."""
    if len(data) < 18:
        raise ValueError("VLESS header too small")
    version = data[0]
    if version not in (0, 1):
        raise ValueError("unsupported VLESS version")
    pos = 17
    addon_len = data[pos]
    pos += 1 + addon_len
    if len(data) < pos + 4:
        raise ValueError("incomplete VLESS header")
    command = data[pos]
    pos += 1
    if command not in (1, 2):
        raise ValueError("unsupported VLESS command")
    port = int.from_bytes(data[pos:pos + 2], "big")
    pos += 2
    address_type = data[pos]
    pos += 1
    if address_type == 1:
        if len(data) < pos + 4:
            raise ValueError("incomplete IPv4 address")
        address = str(ipaddress.IPv4Address(data[pos:pos + 4]))
        pos += 4
    elif address_type == 2:
        if len(data) < pos + 1:
            raise ValueError("incomplete domain length")
        length = data[pos]
        pos += 1
        if not length or len(data) < pos + length:
            raise ValueError("incomplete domain address")
        address = data[pos:pos + length].decode("utf-8", errors="strict")
        pos += length
    elif address_type == 3:
        if len(data) < pos + 16:
            raise ValueError("incomplete IPv6 address")
        address = str(ipaddress.IPv6Address(data[pos:pos + 16]))
        pos += 16
    else:
        raise ValueError("unsupported address type")
    return version, command, address, port, data[pos:]
