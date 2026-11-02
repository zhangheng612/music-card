"""绕过本机 hosts 对 GitHub 的屏蔽（只影响当前进程，不改系统文件）。

有些机器上的 hosts 被写成了 `127.0.0.1 github.com`，导致 requests/urllib 全部连不上。
这里用阿里公共 DNS 的 JSON 接口拿到真实 IP，再把 socket 解析结果替换掉。
"""

import json
import socket
import urllib.request

TARGETS = (
    "github.com",
    "api.github.com",
    "github.io",
    "objects.githubusercontent.com",
    "codeload.github.com",
)

_cache: dict[str, str] = {}


def _resolve(name: str) -> str | None:
    try:
        url = f"https://223.5.5.5/resolve?name={name}&type=1"
        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    for answer in data.get("Answer", []):
        if answer.get("type") == 1:
            return answer.get("data")
    return None


def bypass_dns() -> bool:
    """给 socket 打补丁；返回是否至少解析出一个真实地址。"""
    original = socket.getaddrinfo

    def patched(host, port, *args, **kwargs):
        if isinstance(host, str) and host in TARGETS:
            ip = _cache.get(host) or _resolve(host) or ""
            _cache[host] = ip
            if ip:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
        return original(host, port, *args, **kwargs)

    socket.getaddrinfo = patched

    try:
        with socket.create_connection(("github.com", 443), timeout=6):
            return True
    except Exception:
        return False
