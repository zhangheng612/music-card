"""用本机 Edge（Playwright）截取分享页的移动端预览图。"""

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "preview"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    pages = sorted((ROOT / "dist" / "s").glob("*/index.html"))
    index_url = (ROOT / "dist" / "index.html").as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            locale="zh-CN",
        )
        page = context.new_page()

        for i, song_page in enumerate(pages):
            page.goto(song_page.as_uri() + "#noauto")
            page.wait_for_timeout(500)
            name = "page-song.png" if i == 0 else f"page-song-{i + 1}.png"
            page.screenshot(path=str(OUT / name))

        page.goto(index_url)
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "page-index.png"), full_page=True)

        browser.close()

    print(f"screenshots: {len(pages)} song page(s) + index → {OUT}")


if __name__ == "__main__":
    main()
