"""生成 1600×900 的朋友圈图片卡片（带二维码）。

用法：
  python tools/make_song_card.py --link "https://music.apple.com/cn/album/535754551?i=535754673" --out "超跑女神.png"
  python tools/make_song_card.py --track-id 535754673
"""

from __future__ import annotations

import argparse
import io
import re
import urllib.parse
import urllib.request
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
FONT = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"

WIDTH, HEIGHT = 1600, 900


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def lookup(track_id: str) -> dict:
    url = f"https://itunes.apple.com/lookup?id={track_id}&country=cn&entity=song"
    import json

    payload = json.loads(fetch(url).decode("utf-8", "ignore"))
    if not payload.get("results"):
        raise SystemExit(f"查不到 trackId={track_id}")
    return payload["results"][0]


def download_cover(track: dict) -> Image.Image:
    template = track["artworkUrl100"]
    last = None
    for size in (3000, 2000, 1200, 600):
        url = re.sub(r"/\d+x\d+(bb|bf|wp)?\.(jpg|png)", f"/{size}x{size}bb.jpg", template)
        try:
            image = Image.open(io.BytesIO(fetch(url))).convert("RGB")
            if min(image.size) >= 600:
                return image
        except Exception as error:
            last = error
    raise SystemExit(f"封面下载失败：{last}")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT, size)


def rounded(image: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, image.size[0] - 1, image.size[1] - 1), radius=radius, fill=255
    )
    out = Image.new("RGBA", image.size, (0, 0, 0, 0))
    out.paste(image, (0, 0), mask)
    return out


def fit_square(image: Image.Image, size: int) -> Image.Image:
    """方形裁切；宽幅封面往右偏一点，避免切掉右上角的专辑标识。"""
    centering = (0.85, 0.5) if image.size[0] / image.size[1] > 1.2 else (0.5, 0.5)
    return ImageOps.fit(image, (size, size), Image.LANCZOS, centering=centering)


def glow(canvas: Image.Image, color: tuple[int, int, int]) -> None:
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for radius in range(560, 0, -16):
        alpha = int(26 * (1 - radius / 560))
        draw.ellipse(
            (140 - radius, 90 - radius, 140 + radius, 90 + radius),
            fill=(*color, alpha),
        )
        draw.ellipse(
            (1480 - radius, 880 - radius, 1480 + radius, 880 + radius),
            fill=(*color, alpha // 2),
        )
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(60)))


def draw_text(
    canvas: Image.Image,
    xy: tuple[int, int],
    text: str,
    size: int,
    *,
    bold: bool = False,
    alpha: int = 255,
    tracking: int = 0,
    max_width: int | None = None,
) -> int:
    """返回文字底部 y 坐标。tracking 为字距，仅用于短标签。"""
    draw = ImageDraw.Draw(canvas)
    color = (255, 255, 255, alpha)
    if tracking:
        x, y = xy
        f = font(size, bold)
        for char in text:
            draw.text((x, y), char, font=f, fill=color)
            x += draw.textlength(char, font=f) + tracking
        return y + size

    f = font(size, bold)
    lines: list[str] = []
    if max_width:
        current = ""
        for char in text:
            if draw.textlength(current + char, font=f) <= max_width:
                current += char
            else:
                lines.append(current)
                current = char
        lines.append(current)
        if len(lines) > 2:
            lines = [lines[0], lines[1][:-1] + "…"]
    else:
        lines = [text]

    y = xy[1]
    for line in lines:
        draw.text((xy[0], y), line, font=f, fill=color)
        y += int(size * 1.22)
    return y


def qr_card(url: str, size: int, padding: int, radius: int) -> Image.Image:
    qr = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    code = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    code = code.resize((size - padding * 2, size - padding * 2), Image.NEAREST)

    card = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    white = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    card.paste(white, (0, 0), mask)
    card.paste(code, (padding, padding))
    return card


def render(track: dict, cover: Image.Image) -> Image.Image:
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), (11, 11, 13, 255))

    backdrop = ImageOps.fit(cover, (WIDTH, HEIGHT), Image.LANCZOS)
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(80))
    backdrop = ImageEnhance.Brightness(backdrop).enhance(0.30)
    backdrop = ImageEnhance.Color(backdrop).enhance(1.3)
    canvas.alpha_composite(backdrop.convert("RGBA"))

    small = cover.resize((40, 40))
    avg = tuple(int(c) for c in small.resize((1, 1)).getpixel((0, 0)))
    peak = max(avg)
    accent = tuple(min(255, int(c * (190 / peak)) + 30) if peak else 120 for c in avg)
    glow(canvas, accent)

    shade = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    grad = ImageDraw.Draw(shade)
    for y in range(HEIGHT):
        alpha = int(120 * (y / HEIGHT) ** 1.2)
        grad.line((0, y, WIDTH, y), fill=(0, 0, 0, alpha))
    canvas.alpha_composite(shade)

    # 封面
    side = 600
    art = rounded(fit_square(cover, side), 30)
    x, y = 72, 150
    shadow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (x - 10, y - 10, x + side + 10, y + side + 10), radius=40, fill=(0, 0, 0, 200)
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(40)))
    canvas.alpha_composite(art, (x, y))

    # 播放角标（放在封面右下角，避免挡住人物）
    diameter = 104
    cx, cy = x + side - 78, y + side - 78
    draw = ImageDraw.Draw(canvas)
    draw.ellipse(
        (cx - diameter // 2, cy - diameter // 2, cx + diameter // 2, cy + diameter // 2),
        fill=(255, 255, 255, 242),
    )
    tri = 34
    draw.polygon(
        [(cx - tri // 3, cy - tri), (cx - tri // 3, cy + tri), (cx + tri, cy)],
        fill=(12, 12, 14, 255),
    )

    tx = 740
    draw_text(canvas, (tx, 176), "APPLE MUSIC", 23, bold=True, alpha=115, tracking=6)
    title = (track.get("trackName") or "").strip()
    bottom = draw_text(canvas, (tx, 218), title, 78, bold=True, max_width=760)
    artist = (track.get("artistName") or "").strip()
    bottom = draw_text(canvas, (tx, bottom + 8), artist, 44, alpha=190)
    meta = " · ".join(
        piece
        for piece in (
            (track.get("collectionName") or "").strip(),
            (track.get("releaseDate") or "")[:4],
            (
                f"{int(track['trackTimeMillis']) // 60000}:"
                f"{int(track['trackTimeMillis']) // 1000 % 60:02d}"
                if track.get("trackTimeMillis")
                else ""
            ),
        )
        if piece
    )
    draw_text(canvas, (tx, bottom + 10), meta, 28, alpha=120)

    draw.line((tx, 646, 1290, 646), fill=(255, 255, 255, 40), width=1)
    draw_text(canvas, (tx, 676), "长按识别二维码", 30, bold=True, alpha=240)
    draw_text(canvas, (tx, 722), "在 Apple Music 中打开这首歌", 26, alpha=165)

    storefront = "cn"
    qr_url = f"https://music.apple.com/{storefront}/song/{track['trackId']}"
    canvas.alpha_composite(qr_card(qr_url, 220, 18, 26), (1320, 620))

    return canvas.convert("RGB")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--link", help="Apple Music 链接（含 ?i= 的歌曲链接）")
    parser.add_argument("--track-id", help="Apple Music trackId")
    parser.add_argument("--out", default="song-card.png", help="输出 PNG 路径")
    args = parser.parse_args()

    track_id = args.track_id
    if not track_id and args.link:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(args.link).query)
        track_id = (query.get("i") or [""])[0]
    if not track_id:
        parser.error("需要 --track-id 或带 ?i= 的 --link")

    track = lookup(track_id)
    cover = download_cover(track)
    image = render(track, cover)
    out = Path(args.out)
    image.save(out, "PNG")
    print(f"{track['trackName']} - {track['artistName']} → {out}  {image.size[0]}x{image.size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
