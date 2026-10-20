"""
TikTok liked videos -> Telegram channel

Har CHECK_EVERY soniyada TikTok profilingizdagi "Liked" ro'yxatini tekshiradi,
yangi videolarni yt-dlp bilan yuklaydi va Telegram kanalga yuboradi.

Birinchi ishga tushirishda mavjud layklar "ko'rilgan" deb belgilanadi
(kanalga tashlanmaydi), faqat shundan keyingi yangi layklar yuboriladi.
"""
import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

import requests
from TikTokApi import TikTokApi

# ====== SOZLAMALAR ======
TIKTOK_USERNAME = os.getenv("TIKTOK_USERNAME", "SIZNING_USERNAME")   # @ belgisiz
BOT_TOKEN = os.getenv("BOT_TOKEN", "BOTFATHER_TOKEN")
CHANNEL = os.getenv("CHANNEL", "@kanal_username")                    # yoki -100... ID
MS_TOKEN = os.getenv("MS_TOKEN", "")                                 # tiktok.com cookie: msToken
CHECK_EVERY = int(os.getenv("CHECK_EVERY", "600"))                   # soniya (10 daqiqa)
FETCH_COUNT = 15                                                     # har safar nechta oxirgi layk tekshiriladi
MAX_SIZE = 50 * 1024 * 1024                                          # Bot API limiti
SEEN_FILE = Path("seen.json")
# ========================


def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return None  # None = birinchi ishga tushirish


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen)))


async def fetch_liked():
    """Oxirgi layk bosilgan videolar: [(video_id, url), ...] (yangisi birinchi)."""
    items = []
    async with TikTokApi() as api:
        await api.create_sessions(
            ms_tokens=[MS_TOKEN] if MS_TOKEN else None,
            num_sessions=1,
            sleep_after=3,
            browser="chromium",
        )
        user = api.user(TIKTOK_USERNAME)
        async for video in user.liked(count=FETCH_COUNT):
            vid = video.id
            author = video.author.username
            items.append((vid, f"https://www.tiktok.com/@{author}/video/{vid}"))
    return items


def download(url, outdir):
    """yt-dlp bilan eng yaxshi sifatda yuklaydi, fayl yo'lini qaytaradi."""
    out = os.path.join(outdir, "%(id)s.%(ext)s")
    subprocess.run(
        ["yt-dlp", "-f", "best", "--no-playlist", "-o", out, url],
        check=True, capture_output=True,
    )
    files = list(Path(outdir).glob("*"))
    return files[0] if files else None


def send_to_telegram(path, url):
    size = path.stat().st_size
    if size > MAX_SIZE:
        print(f"  katta ({size/1e6:.0f} MB), o'tkazib yuborildi: {url}")
        return False
    with open(path, "rb") as f:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo",
            data={"chat_id": CHANNEL, "supports_streaming": "true"},
            files={"video": f},
            timeout=300,
        )
    if not r.ok:
        print("  Telegram xato:", r.text)
    return r.ok


async def main():
    seen = load_seen()
    first_run = seen is None
    seen = seen or set()

    while True:
        try:
            items = await fetch_liked()
        except Exception as e:
            print("TikTok'dan o'qishda xato:", e)
            items = []

        if first_run and items:
            seen.update(v for v, _ in items)
            save_seen(seen)
            first_run = False
            print(f"Birinchi ishga tushirish: {len(items)} ta mavjud layk belgilandi.")
        else:
            new = [(v, u) for v, u in items if v not in seen]
            for vid, url in reversed(new):  # eskisidan yangisiga
                print("Yangi:", url)
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        path = download(url, tmp)
                        if path and send_to_telegram(path, url):
                            seen.add(vid)
                            save_seen(seen)
                except Exception as e:
                    print("  xato:", e)

        if os.getenv("RUN_ONCE"):
            break
        await asyncio.sleep(CHECK_EVERY)


if __name__ == "__main__":
    asyncio.run(main())
