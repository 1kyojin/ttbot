"""
TikTok havola -> Telegram kanal

Siz TikTok'da videoni Telegram orqali botga yuborasiz (Share -> Telegram -> bot).
Bot navbatdagi ishga tushganda yangi xabarlardagi TikTok havolalarini topadi,
videoni eng yuqori sifatda yuklaydi (qayta kodlamasdan) va kanalga yuboradi.
Natijani sizga botdan javob sifatida yozadi.

Yuklash tartibi:
  1) yt-dlp (Chrome taqlidi bilan)
  2) yt-dlp (oddiy)
  3) zaxira: tikwm.com ochiq API (TikTok GitHub IP'ni bloklasa)

Faqat kanal adminlari yuborgan havolalar qabul qilinadi.
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL"]  # @kanal_username yoki -100... ID
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
MAX_SIZE = 50 * 1024 * 1024  # Bot API limiti
URL_RE = re.compile(r"https?://(?:[\w-]+\.)*tiktok\.com/\S+", re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def tg(method, **kwargs):
    r = requests.post(f"{API}/{method}", timeout=300, **kwargs)
    try:
        return r.json()
    except Exception:
        return {"ok": False, "description": r.text[:200]}


def say(chat_id, text):
    tg("sendMessage", data={"chat_id": chat_id, "text": text[:3500]})


def admin_ids():
    r = tg("getChatAdministrators", data={"chat_id": CHANNEL})
    if not r.get("ok"):
        print("Adminlarni olib bo'lmadi:", r.get("description"))
        return set()
    return {a["user"]["id"] for a in r["result"]}


# ---------- yuklash ----------

def _first_file(outdir):
    files = [f for f in Path(outdir).iterdir() if f.is_file()]
    if not files:
        raise RuntimeError("fayl topilmadi")
    return files[0]


def run_ytdlp(url, outdir, impersonate):
    cmd = ["yt-dlp", "--no-playlist", "-o", os.path.join(outdir, "%(id)s.%(ext)s")]
    if impersonate:
        cmd += ["--impersonate", "chrome"]
    cmd.append(url)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    if p.returncode != 0:
        lines = [l for l in p.stderr.strip().splitlines() if l.strip()]
        raise RuntimeError(lines[-1][:250] if lines else "yt-dlp xatosi")
    return _first_file(outdir)


def run_tikwm(url, outdir):
    r = requests.get(
        "https://www.tikwm.com/api/",
        params={"url": url, "hd": 1},
        headers={"User-Agent": UA},
        timeout=60,
    )
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(str(j.get("msg") or "tikwm xatosi"))
    d = j["data"]
    link = d.get("hdplay") or d.get("play")
    if not link:
        raise RuntimeError("tikwm video havolasini bermadi")
    if link.startswith("/"):
        link = "https://www.tikwm.com" + link
    path = Path(outdir) / f"{d.get('id', 'video')}.mp4"
    with requests.get(link, stream=True, headers={"User-Agent": UA}, timeout=120) as resp:
        resp.raise_for_status()
        with open(path, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
    return path


def download(url, outdir):
    errors = []
    for imp in (True, False):
        try:
            return run_ytdlp(url, outdir, imp)
        except Exception as e:
            errors.append(f"yt-dlp{' (chrome)' if imp else ''}: {e}")
    try:
        print("  zaxira usul: tikwm")
        return run_tikwm(url, outdir)
    except Exception as e:
        errors.append(f"tikwm: {e}")
    raise RuntimeError(" | ".join(errors))


# ---------- yuborish ----------

def probe(path):
    """O'lcham va davomiylik (vertikal video to'g'ri ko'rinishi uchun)."""
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        d = json.loads(p.stdout)
        s = d["streams"][0]
        return {
            "width": int(s["width"]),
            "height": int(s["height"]),
            "duration": int(float(d["format"]["duration"])),
        }
    except Exception:
        return {}


def post_video(path):
    size = path.stat().st_size
    if size > MAX_SIZE:
        raise RuntimeError(f"video juda katta ({size / 1e6:.0f} MB, limit 50 MB)")
    data = {"chat_id": CHANNEL, "supports_streaming": "true"}
    data.update({k: str(v) for k, v in probe(path).items()})
    with open(path, "rb") as f:
        r = tg("sendVideo", data=data, files={"video": f})
    if not r.get("ok"):
        raise RuntimeError("Telegram: " + str(r.get("description")))


def main():
    admins = admin_ids()
    r = tg("getUpdates", data={"timeout": 0, "allowed_updates": json.dumps(["message"])})
    if not r.get("ok"):
        print("getUpdates xato:", r.get("description"))
        return
    updates = r["result"]
    print(f"Yangi xabarlar: {len(updates)}")
    last = None

    for u in updates:
        last = u["update_id"]
        m = u.get("message") or {}
        text = m.get("text") or m.get("caption") or ""
        chat_id = (m.get("chat") or {}).get("id")
        uid = (m.get("from") or {}).get("id")
        urls = URL_RE.findall(text)
        if not urls or chat_id is None:
            continue
        if uid not in admins:
            print("Ruxsatsiz foydalanuvchi:", uid)
            say(chat_id, "Ruxsat yo'q.")
            continue

        for url in urls:
            print("Havola:", url)
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    post_video(download(url, tmp))
                say(chat_id, "✅ Kanalga yuborildi")
            except Exception as e:
                print("  xato:", e)
                say(chat_id, f"❌ Yuborilmadi: {e}")

    # Ko'rilgan xabarlarni tasdiqlash (Telegram ularni qayta bermaydi)
    if last is not None:
        tg("getUpdates", data={"offset": last + 1, "timeout": 0})


if __name__ == "__main__":
    main()
