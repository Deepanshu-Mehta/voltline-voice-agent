"""Record demo-video segments: a real conversation on the Talk page, with a caption bar.

Playwright records the page as video (no sound); the browser driver records both sides of the
call as a stereo WAV on the page's own clock. This script records one scripted scenario on one
system, then joins picture and sound with ffmpeg, aligned by the page's time origin.

  uv run --project server python evals/demo/record_demo.py --scenario S2-stock-then-price --mode standard \\
      --caption "System A: the cascade" --out evals/demo/out/a-s2.mp4

Synthetic customer voices only: the owner's recordings are never used here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "driver"))

from browser_driver import CLIP_ROOTS, ROOT, STUB, run_scenario  # noqa: E402

CAPTION_JS = """(text) => {
  let bar = document.getElementById('__demo_caption');
  if (!bar) {
    bar = document.createElement('div'); bar.id = '__demo_caption';
    Object.assign(bar.style, {position: 'fixed', left: '0', right: '0', bottom: '0', zIndex: 99999,
      padding: '14px 24px', font: '600 20px/1.35 Archivo, Helvetica, Arial, sans-serif',
      background: 'rgba(29,34,39,0.92)', color: '#fff', textAlign: 'center'});
    document.body.appendChild(bar);
  }
  bar.textContent = text;
  if (!document.getElementById('__demo_hide')) {  // the Next.js development badge is not part of the product
    const st = document.createElement('style'); st.id = '__demo_hide';
    st.textContent = 'nextjs-portal { display: none !important; }';
    document.head.appendChild(st);
  }
}"""


async def record(scenario_id: str, mode: str, caption: str, out: Path, site: str, api: str) -> None:
    from playwright.async_api import async_playwright

    scenario = next(s for s in json.loads((ROOT / "evals" / "scenarios.json").read_text()) if s["id"] == scenario_id)
    work = out.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--use-fake-ui-for-media-stream",
                                                "--autoplay-policy=no-user-gesture-required"])
        context = await browser.new_context(permissions=["microphone"], viewport={"width": 1280, "height": 720},
                                            record_video_dir=str(work), record_video_size={"width": 1280, "height": 720})
        await context.add_init_script(STUB)

        async def serve_clip(route):
            name = route.request.url.split("/__clips/", 1)[1]
            path = (ROOT / name).resolve()
            if not any(r.resolve() in path.parents for r in CLIP_ROOTS[:1]) or not path.exists():  # synthetic only
                await route.fulfill(status=404)
                return
            await route.fulfill(body=path.read_bytes(), content_type="audio/wav")

        await context.route("**/__clips/**", serve_clip)
        video_start = time.time()
        page = await context.new_page()

        async def keep_caption():
            while True:
                try:
                    await page.evaluate(CAPTION_JS, caption)
                except Exception:
                    pass
                await asyncio.sleep(0.5)

        keeper = asyncio.create_task(keep_caption())
        origin = None

        async def note_origin(_):
            nonlocal origin
            origin = await page.evaluate("performance.timeOrigin")

        page.on("load", lambda _: asyncio.ensure_future(note_origin(_)))
        result = await run_scenario(page, site, api, scenario, work, mode)
        await asyncio.sleep(1.5)
        keeper.cancel()
        video_path = await page.video.path()
        await context.close()
        await browser.close()
    t0_ms = result["recording_t0_ms"]
    # Seconds from the start of the video to the first sample of the stereo recording.
    offset = ((origin or video_start * 1000) + t0_ms) / 1000 - video_start
    wav = ROOT / result["recording"]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_path), "-itsoffset", f"{offset:.3f}",
                    "-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-r", "30", "-c:a", "aac", "-ac", "2", "-shortest", str(out)], check=True)
    print(f"{out.relative_to(ROOT)}: {result['id']} on {mode}, audio offset {offset:.2f} s, "
          f"passed={result['passed']} {result['problems']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--mode", choices=["standard", "s2s"], required=True)
    ap.add_argument("--caption", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--site", default="http://localhost:3000")
    ap.add_argument("--api", default="http://localhost:8000")
    args = ap.parse_args()
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(record(args.scenario, args.mode, args.caption, out, args.site, args.api))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
