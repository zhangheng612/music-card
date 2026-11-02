#!/usr/bin/env python3
"""把 songs.txt 里的 Apple Music 链接生成为静态分享页。

每个链接生成：
  dist/s/<trackId>/index.html   分享页（带 og 标签，微信抓取用）
  dist/s/<trackId>/og.jpg       1200x630 分享图（满幅封面，无白边）
  dist/s/<trackId>/art.jpg      1400x1400 页面大图

用法：
  python tools/build_site.py --base-url https://<user>.github.io/<repo>/
  python tools/build_site.py --base-url http://localhost:8000/   # 本地预览
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SONGS_FILE = ROOT / "songs.txt"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"

STORE_PREFIXES = {"album", "song", "music-video", "playlist"}


def log(message: str) -> None:
    print(message, flush=True)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def parse_link(raw: str) -> tuple[str, str, str]:
    """返回 (kind, id, storefront)。kind 是 song 或 album。"""
    text = raw.strip()
    if not text or text.startswith("#"):
        raise ValueError("空行或注释")
    if not re.match(r"^https?://", text):
        text = "https://" + text
    parsed = urllib.parse.urlparse(text)
    host = parsed.netloc.lower()
    if host == "apple.co" or host.endswith(".apple.co"):
        # 跟随一次重定向拿到最终地址
        request = urllib.request.Request(text, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.geturl()
        parsed = urllib.parse.urlparse(text)
        host = parsed.netloc.lower()
    if "music.apple.com" not in host and "itunes.apple.com" not in host:
        raise ValueError(f"不是 Apple Music 链接：{raw.strip()}")

    parts = [p for p in parsed.path.split("/") if p]
    storefront = "cn"
    if parts and len(parts[0]) == 2 and parts[0].isalpha():
        storefront = parts[0].lower()
        parts = parts[1:]

    kind = ""
    item_id = ""
    for index, part in enumerate(parts):
        if part in STORE_PREFIXES and index + 1 < len(parts):
            kind = part
            match = re.match(r"^(\d+)", parts[index + 1])
            item_id = match.group(1) if match else ""
            break

    query = urllib.parse.parse_qs(parsed.query)
    track = (query.get("i") or [""])[0]
    if track.isdigit():
        return "song", track, storefront
    if item_id:
        return ("song" if kind == "song" else "album"), item_id, storefront
    raise ValueError(f"链接里没有歌曲或专辑 ID：{raw.strip()}")


def lookup(item_id: str, storefront: str, entity: str) -> dict | None:
    url = (
        "https://itunes.apple.com/lookup?"
        + urllib.parse.urlencode(
            {"id": item_id, "country": storefront, "entity": entity, "limit": 1}
        )
    )
    payload = json.loads(fetch(url).decode("utf-8", "ignore"))
    for item in payload.get("results", []):
        if item.get("wrapperType") == "track" or item.get("kind") == "song":
            return item
    return None


def lookup_album(album_id: str, storefront: str) -> dict | None:
    """专辑页链接：取专辑里的第一首歌。"""
    url = (
        "https://itunes.apple.com/lookup?"
        + urllib.parse.urlencode(
            {"id": album_id, "country": storefront, "entity": "song", "limit": 25}
        )
    )
    payload = json.loads(fetch(url).decode("utf-8", "ignore"))
    tracks = [
        item
        for item in payload.get("results", [])
        if item.get("wrapperType") == "track" and item.get("kind") == "song"
    ]
    return tracks[0] if tracks else None


def resolve(raw: str) -> tuple[dict, str]:
    """返回 (曲目信息, 实际命中的商店地区)。"""
    kind, item_id, storefront = parse_link(raw)
    english = ["us", "gb", "jp", "tw", "hk"]
    candidates = [storefront] + [s for s in english if s != storefront]
    for region in candidates:
        try:
            track = (
                lookup(item_id, region, "song")
                if kind == "song"
                else lookup_album(item_id, region)
            )
            if track:
                return track, region
        except Exception as error:  # 单个地区失败不影响其他地区
            log(f"    {region} 查询失败：{error}")
    raise RuntimeError(f"没有查到这首歌：{raw.strip()}")


def artwork_url(track: dict, size: int) -> str:
    template = track.get("artworkUrl100") or ""
    if not template:
        raise RuntimeError("这首歌没有封面")
    return re.sub(r"/\d+x\d+(bb|bf|wp)?\.(jpg|png)", f"/{size}x{size}bb.jpg", template)


def download_artwork(track: dict) -> Image.Image:
    last_error: Exception | None = None
    for size in (3000, 2000, 1200, 600):
        try:
            data = fetch(artwork_url(track, size))
            image = Image.open(io.BytesIO(data)).convert("RGB")
            if min(image.size) >= 600:
                return image
        except Exception as error:
            last_error = error
    raise RuntimeError(f"封面下载失败：{last_error}")


def rounded(image: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, image.size[0] - 1, image.size[1] - 1), radius=radius, fill=255
    )
    out = Image.new("RGBA", image.size, (0, 0, 0, 0))
    out.paste(image, (0, 0), mask)
    return out


def fit_square(image: Image.Image, size: int) -> Image.Image:
    """方形裁切；宽幅封面往右偏，避免切掉右上角的专辑标识。"""
    centering = (0.85, 0.5) if image.size[0] / image.size[1] > 1.2 else (0.5, 0.5)
    return ImageOps.fit(image, (size, size), Image.LANCZOS, centering=centering)


def make_og_image(cover: Image.Image, out_path: Path) -> None:
    width, height = 1200, 630
    background = ImageOps.fit(cover, (width, height), Image.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(42))
    background = ImageEnhance.Brightness(background).enhance(0.40)
    canvas = background.convert("RGBA")

    side = 600
    art = rounded(fit_square(cover, side), 26)
    x, y = (width - side) // 2, (height - side) // 2

    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x - 8, y - 8, x + side + 8, y + side + 8), radius=34, fill=(0, 0, 0, 190)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(28)))
    canvas.alpha_composite(art, (x, y))

    # 封面右下角的播放角标
    diameter = 68
    center = (x + side - 18 - diameter // 2, y + side - 18 - diameter // 2)
    badge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    draw.ellipse(
        (
            center[0] - diameter // 2,
            center[1] - diameter // 2,
            center[0] + diameter // 2,
            center[1] + diameter // 2,
        ),
        fill=(255, 255, 255, 235),
    )
    triangle = 22
    draw.polygon(
        [
            (center[0] - triangle // 3, center[1] - triangle),
            (center[0] - triangle // 3, center[1] + triangle),
            (center[0] + triangle, center[1]),
        ],
        fill=(12, 12, 14, 255),
    )
    canvas.alpha_composite(badge)

    canvas.convert("RGB").save(out_path, "JPEG", quality=90, optimize=True, progressive=True)


def make_page_art(cover: Image.Image, out_path: Path) -> None:
    art = fit_square(cover, 1400)
    art.save(out_path, "JPEG", quality=86, optimize=True, progressive=True)


def duration_text(millis: int | None) -> str:
    if not millis:
        return ""
    seconds = int(millis) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def meta_line(track: dict) -> str:
    pieces = [
        (track.get("collectionName") or "").strip(),
        (track.get("releaseDate") or "")[:4],
        duration_text(track.get("trackTimeMillis")),
    ]
    return " · ".join(piece for piece in pieces if piece)


def store_link(track: dict, storefront: str) -> str:
    if track.get("trackViewUrl"):
        return track["trackViewUrl"]
    return f"https://music.apple.com/{storefront}/song/{track.get('trackId')}"


SONG_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title} - {artist}</title>
<meta name="theme-color" content="#0b0b0d">
<meta property="og:type" content="music.song">
<meta property="og:site_name" content="Apple Music">
<meta property="og:title" content="{title} - {artist}">
<meta property="og:description" content="{meta}">
<meta property="og:image" content="{base}s/{track_id}/og.jpg">
<meta property="og:image:secure_url" content="{base}s/{track_id}/og.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:type" content="image/jpeg">
<meta property="og:url" content="{base}s/{track_id}/">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title} - {artist}">
<meta name="twitter:description" content="{meta}">
<meta name="twitter:image" content="{base}s/{track_id}/og.jpg">
<meta name="description" content="{title} - {artist} · {meta}">
<link rel="image_src" href="{base}s/{track_id}/og.jpg">
<link rel="canonical" href="{base}s/{track_id}/">
<link rel="stylesheet" href="../../assets/style.css">
</head>
<body>
<div class="song" data-redirect="{store_link}">
<div class="song__bg" style="background-image:url('art.jpg')"></div>
<main class="song__inner">
  <p class="eyebrow">APPLE MUSIC</p>
  <img class="cover" src="art.jpg" width="1400" height="1400" alt="{title} 封面">
  <h1 class="title">{title}</h1>
  <p class="artist">{artist}</p>
  <p class="meta">{meta}</p>
  <a class="button" href="{store_link}">在 Apple Music 中打开</a>
  <p class="hint">点上面的按钮，就能在 Apple Music 里打开这首歌。</p>
</main>
</div>
<script>
(function () {{
  // 默认不自动跳转，避免微信抓分享卡片时抓到 Apple 的页面；?auto=1 才自动打开
  var query = window.location.search;
  if (query.indexOf('auto=1') === -1) return;
  var target = document.querySelector('.song').getAttribute('data-redirect');
  if (!target) return;
  setTimeout(function () {{ window.location.replace(target); }}, 900);
}})();
</script>
</body>
</html>
"""

INDEX_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>音乐卡片 · 分享页</title>
<meta name="theme-color" content="#0b0b0d">
<meta property="og:type" content="website">
<meta property="og:title" content="音乐卡片 · 分享页">
<meta property="og:description" content="点开任意一首，直接在 Apple Music 里收听。">
<link rel="stylesheet" href="assets/style.css">
</head>
<body class="index">
<header class="index__header">
  <p class="eyebrow">APPLE MUSIC</p>
  <h1>音乐卡片</h1>
  <p class="index__lead">点开任意一首，就能在 Apple Music 里收听。</p>
</header>
<main class="grid">
{cards}
</main>
<footer class="index__footer">
  <p>由 GitHub Pages 静态托管 · 分享页由 songs.txt 自动生成</p>
</footer>
</body>
</html>
"""

CARD_TEMPLATE = """  <a class="card" href="s/{track_id}/">
    <img class="card__art" src="s/{track_id}/art.jpg" alt="" loading="lazy" width="1400" height="1400">
    <span class="card__text">
      <strong>{title}</strong>
      <span>{artist}</span>
    </span>
    <span class="card__play" aria-hidden="true"></span>
  </a>"""


def build_song(track: dict, storefront: str, base: str, cover_cache: dict[str, Image.Image]) -> str:
    track_id = str(track["trackId"])
    title = (track.get("trackName") or "未知歌曲").strip()
    artist = (track.get("artistName") or "未知歌手").strip()
    meta = meta_line(track)
    target = ROOT / "dist" / "s" / track_id
    target.mkdir(parents=True, exist_ok=True)

    cache_key = track.get("artworkUrl100") or track_id
    if cache_key not in cover_cache:
        cover_cache[cache_key] = download_artwork(track)
        log(f"    封面 {cover_cache[cache_key].size}")
    cover = cover_cache[cache_key]

    make_og_image(cover, target / "og.jpg")
    make_page_art(cover, target / "art.jpg")

    page = SONG_TEMPLATE.format(
        title=html.escape(title),
        artist=html.escape(artist),
        meta=html.escape(meta),
        track_id=track_id,
        base=base,
        store_link=html.escape(store_link(track, storefront), quote=True),
    )
    (target / "index.html").write_text(page, encoding="utf-8")
    log(f"    {title} - {artist}  →  s/{track_id}/")
    return CARD_TEMPLATE.format(
        track_id=track_id, title=html.escape(title), artist=html.escape(artist)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="站点根地址，例如 https://user.github.io/repo/")
    parser.add_argument("--domain", default="", help="自定义域名，例如 music.example.com（会写入 dist/CNAME）")
    args = parser.parse_args()

    base = args.base_url.strip()
    if not base.endswith("/"):
        base += "/"

    if not SONGS_FILE.exists():
        log(f"找不到 {SONGS_FILE}")
        return 1

    raw_lines = [
        line.strip()
        for line in SONGS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not raw_lines:
        log("songs.txt 里还没有链接")
        return 1

    if DIST.exists():
        import shutil

        shutil.rmtree(DIST)
    (DIST / "assets").mkdir(parents=True)
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    (DIST / "assets" / "style.css").write_text(
        (ROOT / "assets" / "style.css").read_text(encoding="utf-8"), encoding="utf-8"
    )

    cards: list[str] = []
    seen: set[str] = set()
    cover_cache: dict[str, Image.Image] = {}
    failures = 0

    for line in raw_lines:
        log(f"处理 {line}")
        try:
            track, storefront = resolve(line)
            track_id = str(track.get("trackId") or "")
            if not track_id or track_id in seen:
                log("    跳过（重复或没有 ID）")
                continue
            seen.add(track_id)
            cards.append(build_song(track, storefront, base, cover_cache))
        except Exception as error:
            failures += 1
            log(f"    失败：{error}")

    (DIST / "index.html").write_text(
        INDEX_TEMPLATE.format(cards="\n".join(cards)), encoding="utf-8"
    )
    domain = args.domain.strip().lower().rstrip(".")
    if domain:
        (DIST / "CNAME").write_text(domain + "\n", encoding="utf-8")
        log(f"自定义域名：{domain}（已写入 CNAME）")
    log(f"完成：{len(seen)} 首，失败 {failures} 首 → {DIST}")
    return 0 if seen else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
