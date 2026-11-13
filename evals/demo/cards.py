"""Title and result cards for the demo video, rendered by the browser (this ffmpeg build has no
text filters), then turned into silent video segments, or segments with a sound clip under them.

  from cards import card_video
  card_video(html_body, seconds, out_mp4, audio=None)
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

STYLE = """
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..800&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  html, body { margin: 0; width: 1280px; height: 720px; }
  body { background: #eef1f4; color: #1d2227; font: 400 26px/1.45 Archivo, Helvetica, Arial, sans-serif;
         display: flex; flex-direction: column; justify-content: center; padding: 0 96px; box-sizing: border-box; gap: 22px; }
  h1 { font: 780 64px/1.05 Archivo, sans-serif; font-variation-settings: "wdth" 112; margin: 0; letter-spacing: -0.5px; }
  h2 { font: 720 44px/1.1 Archivo, sans-serif; font-variation-settings: "wdth" 108; margin: 0; }
  p { margin: 0; max-width: 1000px; }
  .muted { color: #5b6573; }
  .tag { font: 500 18px 'IBM Plex Mono', monospace; color: #2456c9; letter-spacing: 0.5px; }
  table { border-collapse: collapse; font-size: 24px; font-variant-numeric: tabular-nums; }
  th, td { text-align: left; padding: 9px 28px 9px 0; border-bottom: 1px solid #cdd3da; }
  th { font-weight: 700; color: #5b6573; font-size: 20px; }
  td.num { text-align: right; padding-right: 36px; }
  .good { color: #2f7a4a; font-weight: 700; } .bad { color: #b3261e; font-weight: 700; }
  .quote { font: 500 30px/1.4 'IBM Plex Mono', monospace; background: #fff; border: 1px solid #cdd3da;
           border-radius: 10px; padding: 18px 24px; }
</style>"""


async def _render(html: str, png: Path) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        await page.set_content(f"<!doctype html><html><head><meta charset='utf-8'>{STYLE}</head><body>{html}</body></html>",
                               wait_until="domcontentloaded")
        try:  # fonts come from Google Fonts; never let a slow request stop the build
            await page.wait_for_function("document.fonts.status === 'loaded'", timeout=8000)
        except Exception:
            pass
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(png))
        await browser.close()


def card_video(html: str, seconds: float, out: Path, audio: Path | None = None) -> Path:
    png = out.with_suffix(".png")
    asyncio.run(_render(html, png))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(png)]
    if audio:
        cmd += ["-i", str(audio), "-af", "apad", "-t", f"{seconds:.2f}"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{seconds:.2f}"]
    cmd += ["-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", str(out)]
    subprocess.run(cmd, check=True)
    return out


def concat(parts: list[Path], out: Path) -> Path:
    """Join segments; re-encode so mismatched audio rates and timestamps cannot glitch."""
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-ar", "48000", "-ac", "2",
                    str(out)], check=True)
    return out
