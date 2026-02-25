#!/usr/bin/env python3
"""
YouTube Downloader Bot — Pyrogram + yt-dlp
Commands:
  /yl <url>     — pick quality & download
  /setcookie    — reply to a cookie .txt file to set it
  /delcookie    — delete saved cookie
  /cookie       — show cookie status
"""

import os, asyncio, time, math, shutil
from dotenv import load_dotenv
load_dotenv()
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
import yt_dlp

# ─────────────────────────────────────────────────────────────
# CONFIG — loaded from environment variables
# ─────────────────────────────────────────────────────────────
API_ID      = int(os.environ["API_ID"])        # my.telegram.org API ID
API_HASH    = os.environ["API_HASH"]           # my.telegram.org API Hash
BOT_TOKEN   = os.environ["BOT_TOKEN"]          # @BotFather token

DOWNLOAD_DIR  = os.environ.get("DOWNLOAD_DIR", "./downloads")
COOKIE_PATH   = os.environ.get("COOKIE_PATH",  "./cookies.txt")
MAX_TG_SIZE   = int(os.environ.get("MAX_TG_SIZE", 2_000_000_000))

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Client("ytdl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def human_size(b: int) -> str:
    if b <= 0: return "0B"
    units = ["B","KB","MB","GB","TB"]
    i = min(int(math.floor(math.log(max(b,1), 1024))), len(units)-1)
    return f"{b/1024**i:.2f}{units[i]}"

def human_time(s: float) -> str:
    s = max(int(s), 0)
    if s < 60:   return f"{s}s"
    if s < 3600: return f"{s//60}m{s%60:02d}s"
    return f"{s//3600}h{(s%3600)//60}m{s%60:02d}s"

def progress_bar(pct: float, width: int = 12) -> str:
    filled = int(width * pct / 100)
    return "█" * filled + "░" * (width - filled)

def cookie_active() -> bool:
    return os.path.exists(COOKIE_PATH) and os.path.getsize(COOKIE_PATH) > 0

def base_ydl_opts() -> dict:
    opts = {"quiet": True, "no_warnings": True, "nocheckcertificate": True}
    if cookie_active():
        opts["cookiefile"] = COOKIE_PATH
    return opts

# ─────────────────────────────────────────────────────────────
# FORMAT FETCHER
# ─────────────────────────────────────────────────────────────
def fetch_info(url: str) -> dict:
    opts = base_ydl_opts()
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def build_keyboard(info: dict, uid: str) -> InlineKeyboardMarkup:
    formats = info.get("formats", [])

    # best (format_id, fps, filesize) per (height, ext)
    best: dict[tuple, tuple] = {}
    for f in formats:
        h   = f.get("height")
        ext = f.get("ext", "")
        if not h or not f.get("vcodec") or f["vcodec"] == "none":
            continue
        sz  = f.get("filesize") or f.get("filesize_approx") or 0
        fps = int(f.get("fps") or 24)
        key = (h, ext)
        if key not in best or sz > best[key][2]:
            best[key] = (f["format_id"], fps, sz)

    rows = []

    # Audio-only row
    rows.append([
        InlineKeyboardButton("🎵 opus-webm",   callback_data=f"dl|{uid}|bestaudio[ext=webm]"),
        InlineKeyboardButton("🎵 mp4a-m4a",    callback_data=f"dl|{uid}|bestaudio[ext=m4a]"),
    ])

    # Video rows sorted by resolution
    heights = sorted(set(h for h, e in best.keys()))
    for h in heights:
        row = []
        for ext in ("mp4", "webm"):
            if (h, ext) in best:
                fid, fps, sz = best[(h, ext)]
                sz_str = f" ({human_size(sz)})" if sz else " (0B)"
                label = f"{h}p{fps}-{ext}{sz_str}"
                row.append(InlineKeyboardButton(label, callback_data=f"dl|{uid}|{fid}"))
            else:
                row.append(InlineKeyboardButton(
                    f"{h}p-{ext} (0B)",
                    callback_data=f"dl|{uid}|bestvideo[height<={h}][ext={ext}]+bestaudio/best"
                ))
        rows.append(row)

    # MP3 / Audio
    rows.append([
        InlineKeyboardButton("🎧 MP3",          callback_data=f"dl|{uid}|mp3"),
        InlineKeyboardButton("🎶 Best Audio",   callback_data=f"dl|{uid}|bestaudio/best"),
    ])
    # Best Video / Best
    rows.append([
        InlineKeyboardButton("🎬 Best Video",   callback_data=f"dl|{uid}|bestvideo+bestaudio/best"),
        InlineKeyboardButton("⚡ Best (auto)",  callback_data=f"dl|{uid}|best"),
    ])
    # Cancel
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"dl|{uid}|cancel")])

    return InlineKeyboardMarkup(rows)

# ─────────────────────────────────────────────────────────────
# DOWNLOAD STATE
# ─────────────────────────────────────────────────────────────
class DLState:
    def __init__(self):
        self.pct = 0.0;        self.speed = 0
        self.eta = 0;          self.downloaded = 0
        self.total = 0;        self.elapsed = 0.0
        self.done = False;     self.error = None
        self.filepath = None;  self.title = "Video"
        self._start = time.time()

def make_hook(state: DLState):
    def hook(d):
        state.elapsed = time.time() - state._start
        if d["status"] == "downloading":
            state.downloaded = d.get("downloaded_bytes") or 0
            state.total      = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            state.speed      = d.get("speed") or 0
            state.eta        = d.get("eta") or 0
            state.pct        = (state.downloaded / state.total * 100) if state.total else 0
        elif d["status"] == "finished":
            state.pct      = 100
            state.filepath = d.get("filename")
            state.elapsed  = time.time() - state._start
    return hook

def run_download(url: str, fmt_id: str, out_dir: str, state: DLState):
    is_mp3 = fmt_id == "mp3"
    outtmpl = os.path.join(out_dir, "%(title)s.%(ext)s")

    opts = base_ydl_opts()
    opts.update({
        "outtmpl":             outtmpl,
        "progress_hooks":      [make_hook(state)],
        "merge_output_format": "mp4",
        "writethumbnail":      False,
    })

    if is_mp3:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }]
    else:
        opts["format"] = fmt_id

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            state.title = info.get("title", "Video")
            fp = ydl.prepare_filename(info)
            if is_mp3:
                fp = os.path.splitext(fp)[0] + ".mp3"
            if not state.filepath:
                state.filepath = fp
        state.done = True
    except Exception as e:
        state.error = str(e)
        state.done  = True

# ─────────────────────────────────────────────────────────────
# TEXT BUILDERS
# ─────────────────────────────────────────────────────────────
def progress_text(state: DLState, title: str, url: str) -> str:
    bar   = progress_bar(state.pct)
    speed = f"{human_size(int(state.speed))}/s" if state.speed else "0B/s"
    eta   = human_time(state.eta) if state.eta else "-"
    elap  = human_time(state.elapsed)
    dl    = human_size(state.downloaded)
    tot   = human_size(state.total) if state.total else "?"
    ck    = "🍪 Active" if cookie_active() else "🔓 None"
    ver   = yt_dlp.version.__version__
    return (
        f"📥 **{title}**\n\n"
        f"┌ `{bar}` {state.pct:.1f}%\n"
        f"├ 💾 Processed → {dl} of {tot}\n"
        f"├ ⚡ Status → Downloading\n"
        f"├ 🚀 Speed → {speed}\n"
        f"├ ⏱ Time → -{eta}  ( {elap} )\n"
        f"├ ⚙️ Engine → yt-dlp v{ver}\n"
        f"└ 🍪 Cookie → {ck}"
    )

def done_text(state: DLState) -> str:
    sz = "?"
    if state.filepath and os.path.exists(state.filepath):
        sz = human_size(os.path.getsize(state.filepath))
    return (
        f"✅ **Download Complete!**\n\n"
        f"🎬 **{state.title}**\n"
        f"💾 Size: {sz}\n"
        f"⏱ Time: {human_time(state.elapsed)}"
    )

def refresh_markup(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data=f"ref|{uid}")
    ]])

# ─────────────────────────────────────────────────────────────
# IN-MEMORY SESSION STORE  { uid -> {url, info, state} }
# ─────────────────────────────────────────────────────────────
STORE: dict[str, dict] = {}

# ─────────────────────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────────────────────
@app.on_message(filters.command("start"))
async def cmd_start(client: Client, msg: Message):
    await msg.reply_text(
        "👋 **YouTube Downloader Bot**\n\n"
        "**Commands:**\n"
        "`/yl <url>` — Download a YouTube video\n"
        "`/setcookie` — Reply to a Netscape cookie `.txt` file\n"
        "`/delcookie` — Remove saved cookie\n"
        "`/cookie` — Show cookie status",
        quote=True
    )

@app.on_message(filters.command("cookie"))
async def cmd_cookie(client: Client, msg: Message):
    if cookie_active():
        sz = human_size(os.path.getsize(COOKIE_PATH))
        await msg.reply_text(f"🍪 **Cookie active** — {sz}", quote=True)
    else:
        await msg.reply_text("❌ No cookie set.", quote=True)

@app.on_message(filters.command("delcookie"))
async def cmd_delcookie(client: Client, msg: Message):
    if os.path.exists(COOKIE_PATH):
        os.remove(COOKIE_PATH)
        await msg.reply_text("🗑️ Cookie deleted.", quote=True)
    else:
        await msg.reply_text("⚠️ No cookie to delete.", quote=True)

@app.on_message(filters.command("setcookie"))
async def cmd_setcookie(client: Client, msg: Message):
    """
    Send a Netscape .txt cookie file, then reply to it with /setcookie
    OR send the file with caption /setcookie
    """
    target = msg.reply_to_message if msg.reply_to_message else msg
    doc = getattr(target, "document", None)

    if not doc:
        await msg.reply_text(
            "📎 **How to set a cookie:**\n"
            "1. Export cookies from your browser as Netscape format `.txt`\n"
            "2. Send the `.txt` file to this bot\n"
            "3. Reply to that file with `/setcookie`\n\n"
            "💡 Use extension: **Get cookies.txt LOCALLY**",
            quote=True
        )
        return

    if not doc.file_name.endswith(".txt"):
        await msg.reply_text("⚠️ Please send a `.txt` cookie file.", quote=True)
        return

    m = await msg.reply_text("⬇️ Saving cookie file…", quote=True)
    await client.download_media(target, file_name=COOKIE_PATH)
    sz = human_size(os.path.getsize(COOKIE_PATH))
    await m.edit(
        f"✅ **Cookie saved!**\n"
        f"📄 `{doc.file_name}`\n"
        f"💾 Size: {sz}\n\n"
        f"yt-dlp will now use this cookie for all downloads."
    )

@app.on_message(filters.command("yl"))
async def cmd_yl(client: Client, msg: Message):
    args = msg.command[1:]
    if not args:
        await msg.reply_text("Usage: `/yl <YouTube URL>`", quote=True)
        return

    url = args[0].strip()
    m   = await msg.reply_text("🔍 Fetching video info…", quote=True)

    loop = asyncio.get_event_loop()
    try:
        info = await loop.run_in_executor(None, fetch_info, url)
    except Exception as e:
        await m.edit(f"❌ **Error fetching info:**\n`{e}`")
        return

    title    = info.get("title", "Unknown")
    duration = info.get("duration", 0)

    uid = f"{msg.chat.id}_{m.id}"
    STORE[uid] = {"url": url, "info": info, "state": None}

    keyboard = build_keyboard(info, uid)
    ck_note  = "\n🍪 Cookie: Active" if cookie_active() else ""
    text = (
        f"🎬 **{title}**\n"
        f"⏱ Duration: {human_time(duration)}{ck_note}\n\n"
        f"Choose quality:"
    )

    await m.edit(text, reply_markup=keyboard)

# ─────────────────────────────────────────────────────────────
# CALLBACK — QUALITY SELECT
# ─────────────────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^dl\|"))
async def on_dl(client: Client, cq: CallbackQuery):
    parts = cq.data.split("|", 2)   # ["dl", uid, fmt]
    if len(parts) < 3:
        await cq.answer("Invalid session.", show_alert=True)
        return

    _, uid, fmt = parts

    if fmt == "cancel":
        STORE.pop(uid, None)
        await cq.message.edit("❌ Cancelled.")
        return

    rec = STORE.get(uid)
    if not rec:
        await cq.answer("Session expired. Use /yl again.", show_alert=True)
        return

    # Prevent double start
    if rec.get("state") and not rec["state"].done:
        await cq.answer("Already downloading!", show_alert=True)
        return

    await cq.answer("▶️ Starting download…")

    url   = rec["url"]
    state = DLState()
    state.title = rec["info"].get("title", "Video")
    rec["state"] = state

    out_dir = os.path.join(DOWNLOAD_DIR, uid.replace("|","_").replace(":","_"))
    os.makedirs(out_dir, exist_ok=True)

    await cq.message.edit(
        progress_text(state, state.title, url),
        reply_markup=refresh_markup(uid)
    )

    loop = asyncio.get_event_loop()

    async def run_and_upload():
        # Download in thread pool
        await loop.run_in_executor(None, run_download, url, fmt, out_dir, state)

        if state.error:
            try:
                await cq.message.edit(f"❌ **Download failed:**\n`{state.error}`")
            except Exception:
                pass
            shutil.rmtree(out_dir, ignore_errors=True)
            return

        # Show done card
        try:
            await cq.message.edit(done_text(state))
        except Exception:
            pass

        # Find file
        fp = state.filepath
        if not fp or not os.path.exists(fp):
            files = list(Path(out_dir).iterdir())
            fp = str(files[0]) if files else None

        if not fp or not os.path.exists(fp):
            await cq.message.reply("⚠️ Downloaded file not found.")
            shutil.rmtree(out_dir, ignore_errors=True)
            return

        sz = os.path.getsize(fp)
        if sz > MAX_TG_SIZE:
            await cq.message.reply(
                f"⚠️ File too large ({human_size(sz)}) for Telegram.\n"
                f"Saved locally at:\n`{fp}`"
            )
            return

        ext = os.path.splitext(fp)[1].lower()
        cap = f"🎬 **{state.title}**"

        try:
            if ext in (".mp4", ".mkv", ".webm"):
                await cq.message.reply_video(fp, caption=cap, supports_streaming=True)
            elif ext in (".mp3", ".m4a", ".opus", ".ogg", ".flac"):
                await cq.message.reply_audio(fp, caption=cap, title=state.title)
            else:
                await cq.message.reply_document(fp, caption=cap)
        except Exception as e:
            await cq.message.reply(f"❌ Upload failed:\n`{e}`")

        shutil.rmtree(out_dir, ignore_errors=True)
        STORE.pop(uid, None)

    async def auto_refresh():
        """Update progress every 5 seconds automatically."""
        while not state.done:
            await asyncio.sleep(5)
            if state.done:
                break
            try:
                await cq.message.edit(
                    progress_text(state, state.title, url),
                    reply_markup=refresh_markup(uid)
                )
            except Exception:
                pass

    asyncio.create_task(auto_refresh())
    asyncio.create_task(run_and_upload())

# ─────────────────────────────────────────────────────────────
# CALLBACK — REFRESH BUTTON
# ─────────────────────────────────────────────────────────────
@app.on_callback_query(filters.regex(r"^ref\|"))
async def on_refresh(client: Client, cq: CallbackQuery):
    uid = cq.data.split("|", 1)[1]
    rec = STORE.get(uid)
    if not rec or not rec.get("state"):
        await cq.answer("No active download found.", show_alert=True)
        return

    state = rec["state"]
    await cq.answer("🔄 Refreshed!")

    try:
        if state.done:
            if state.error:
                await cq.message.edit(f"❌ **Error:**\n`{state.error}`")
            else:
                await cq.message.edit(done_text(state))
        else:
            await cq.message.edit(
                progress_text(state, state.title, rec["url"]),
                reply_markup=refresh_markup(uid)
            )
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🤖 Bot starting…  Ctrl+C to stop.")
    app.run()
