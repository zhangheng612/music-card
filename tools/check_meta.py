"""检查分享页的 Open Graph 标签，模拟微信抓取链接时的行为。

用法：
  python tools/check_meta.py https://<user>.github.io/<repo>/s/1442430979/
"""

from __future__ import annotations

import io
import sys
import urllib.request
from html.parser import HTMLParser

from PIL import Image

WECHAT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.50(0x1800322b) NetType/WIFI Language/zh_CN"
)


class MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "meta":
            key = values.get("property") or values.get("name") or ""
            if key:
                self.meta[key] = values.get("content", "")
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def fetch(url: str) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": WECHAT_UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(), response.geturl()


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：python tools/check_meta.py <分享页地址>")
        return 2

    url = sys.argv[1]
    print(f"抓取 {url}")
    print(f"UA   {WECHAT_UA[:60]}…")

    try:
        body, final_url = fetch(url)
    except Exception as error:
        print(f"× 打不开：{error}")
        return 1

    parser = MetaParser()
    parser.feed(body.decode("utf-8", "ignore"))

    print(f"\n最终地址  {final_url}")
    print(f"<title>   {parser.title.strip()}")
    for key in (
        "og:type",
        "og:title",
        "og:description",
        "og:image",
        "og:image:width",
        "og:image:height",
    ):
        print(f"{key:<16} {parser.meta.get(key, '（缺失）')}")

    image_url = parser.meta.get("og:image", "")
    if not image_url:
        print("\n× 没有 og:image，微信会退回普通链接（可能又是白边小图）")
        return 1

    try:
        data, _ = fetch(image_url)
        image = Image.open(io.BytesIO(data))
        print(f"\n分享图  {image.size[0]}×{image.size[1]}  {len(data) / 1024:.0f} KB")
        if image.size[0] < 1000:
            print("! 图片偏小，朋友圈缩略图会糊")
    except Exception as error:
        print(f"\n× 分享图下载失败：{error}")

    print("\n✓ 标签齐全，可以拿去微信里测试了")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
