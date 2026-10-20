"""
TikTok havola -> Telegram kanal

Siz TikTok'da videoni Telegram orqali botga yuborasiz (Share -> Telegram -> bot).
Bot navbatdagi ishga tushganda yangi xabarlardagi TikTok havolalarini topadi,
videoni BIR NECHTA manbadan yuklaydi (tikwm HD va yt-dlp), PyAV bilan
o'lchaydi va eng yaxshisini (ovoz -> fps -> piksel -> bitreyt) kanalga
qayta kodlamasdan yuboradi. Natijani (manba, o'lcham, fps, bitreyt) sizga
botdan javob sifatida yozadi.

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


# ---------- yuklash usullari ----------

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
        raise RuntimeError(lines[-1][:200] if lines else "yt-dlp xatosi")
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


# ---------- o'lchash va tanlash ----------

def probe_full(path):
    """Video o'lchamlari (PyAV orqali, ffprobe kerak emas). Xato bo'lsa ham qaytaradi."""
    size = Path(path).stat().st_size
    info = {"width": 0, "height": 0, "fps": 0.0, "duration": 0, "bitrate": 0,
            "codec": "?", "audio": True, "size": size, "measured": False}
    try:
        import av
        with av.open(str(path)) as c:
            v = next((s for s in c.streams if s.type == "video"), None)
            if v is None:
                raise RuntimeError("video oqimi yo'q")
            info["audio"] = any(s.type == "audio" for s in c.streams)
            rate = v.average_rate or v.guessed_rate
            info["fps"] = round(float(rate), 1) if rate else 0.0
            info["width"] = int(v.codec_context.width or 0)
            info["height"] = int(v.codec_context.height or 0)
            info["codec"] = v.codec_context.name or "?"
            dur = float(c.duration) / 1_000_000 if c.duration else 0.0
            info["duration"] = int(dur)
            if dur:
                info["bitrate"] = int(size * 8 / dur)
            info["measured"] = True
    except Exception as e:
        print("  o'lchab bo'lmadi:", e)
    return info


def score(c):
    # o'lchangan > ovoz bor > fps > piksel soni > bitreyt
    return (c["measured"], c["audio"], round(c["fps"]), c["width"] * c["height"], c["bitrate"] or c["size"])


def describe(c):
    if not c.get("measured"):
        return f"{c['name']}: o'lchab bo'lmadi, {c['size'] / 1e6:.1f} MB"
    return (f"{c['name']}: {c['width']}x{c['height']}, {c['fps']:g} fps, "
            f"{c['bitrate'] / 1e6:.1f} Mbit/s, {c['codec']}, "
            f"{'ovoz bor' if c['audio'] else 'OVOZSIZ'}, {c['size'] / 1e6:.1f} MB")


def gather(url, root):
    """Hamma usullarni sinab, o'lchangan nomzodlar va xatolar ro'yxatini qaytaradi."""
    methods = [
        ("tikwm", lambda d: run_tikwm(url, d)),
        ("yt-dlp", lambda d: run_ytdlp(url, d, True)),
    ]
    candidates, errors = [], []
    ytdlp_ok = False
    for i, (name, fn) in enumerate(methods):
        sub = Path(root) / str(i)
        sub.mkdir()
        try:
            path = fn(str(sub))
            info = probe_full(path)
            info.update(name=name, path=path)
            candidates.append(info)
            if name == "yt-dlp":
                ytdlp_ok = True
        except Exception as e:
            errors.append(f"{name}: {e}")
    if not ytdlp_ok:
        sub = Path(root) / "plain"
        sub.mkdir()
        try:
            path = run_ytdlp(url, str(sub), False)
            info = probe_full(path)
            info.update(name="yt-dlp (oddiy)", path=path)
            candidates.append(info)
        except Exception as e:
            errors.append(f"yt-dlp (oddiy): {e}")
    return candidates, errors


# ---------- yuborish ----------

def post_video(c):
    data = {"chat_id": CHANNEL, "supports_streaming": "true"}
    if c["width"] and c["height"]:
        data["width"] = str(c["width"])
        data["height"] = str(c["height"])
    if c["duration"]:
        data["duration"] = str(c["duration"])
    with open(c["path"], "rb") as f:
        r = tg("sendVideo", data=data, files={"video": f})
    if not r.get("ok"):
        raise RuntimeError("Telegram: " + str(r.get("description")))


def process(url):
    """Yuborilgan video haqida hisobot matnini qaytaradi."""
    with tempfile.TemporaryDirectory() as tmp:
        candidates, errors = gather(url, tmp)
        if not candidates:
            raise RuntimeError(" | ".join(errors) or "yuklab bo'lmadi")
        ordered = sorted(candidates, key=score, reverse=True)
        fitting = [c for c in ordered if c["size"] <= MAX_SIZE]
        if not fitting:
            raise RuntimeError(f"video juda katta (limit 50 MB): {describe(ordered[0])}")
        best = fitting[0]
        post_video(best)
        lines = ["✅ Kanalga yuborildi", "Tanlandi -> " + describe(best)]
        for c in ordered:
            if c is not best:
                lines.append("Boshqasi -> " + describe(c))
        for e in errors:
            lines.append("Xato -> " + e)
        return "\n".join(lines)


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
                report = process(url)
                print(report)
                say(chat_id, report)
            except Exception as e:
                print("  xato:", e)
                say(chat_id, f"❌ Yuborilmadi: {e}")

    # Ko'rilgan xabarlarni tasdiqlash (Telegram ularni qayta bermaydi)
    if last is not None:
        tg("getUpdates", data={"offset": last + 1, "timeout": 0})


if __name__ == "__main__":
    main()
