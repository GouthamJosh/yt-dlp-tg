#!/usr/bin/env python3
"""
YouTube Downloader Bot — Pyrogram + yt-dlp
Commands:
  /start     — Start the bot
  /help      — Show help message
  /dl <url>  — Download YouTube video or audio
  /mp3 <url> — Download YouTube audio as MP3
  /mp4 <url> — Download YouTube video as MP4
  /setcookie — Reply to a cookie .txt file to bypass bot detection
  /delcookie — Delete saved cookie
  /cookie    — Show cookie status
"""

import os
import sys
import asyncio
import time
import math
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()

from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, InputMediaPhoto, InputMediaDocument
)
import yt_dlp

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────
API_ID   = int(os.environ.get("API_ID", "0"))
API_HASH  = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "./downloads")
COOKIE_PATH  = os.environ.get("COOKIE_PATH", "./cookies.txt")
MAX_TG_SIZE  = int(os.environ.get("MAX_TG_SIZE", 2_000_000_000))
MAX_DURATION = int(os.environ.get("MAX_DURATION", 7200))          # 2 hours

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(os.path.dirname(COOKIE_PATH) if os.path.dirname(COOKIE_PATH) else ".", exist_ok=True)

# ─────────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────

def human_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "0B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = min(int(math.floor(math.log(max(size_bytes, 1), 1024))), len(units) - 1)
    return f"{size_bytes / 1024**i:.2f} {units[i]}"

def human_time(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds//60}m{seconds%60:02d}s"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h{m}m{s:02d}s"

def progress_bar(percentage: float, width: int = 10) -> str:
    filled = int(width * percentage / 100)
    return f"[{'█'*filled}{'░'*(width-filled)}]"

def format_file_size(size: Optional[int]) -> str:
    return human_size(size) if size else "Unknown"

def cookie_active() -> bool:
    return (
        os.path.exists(COOKIE_PATH) and
        os.path.getsize(COOKIE_PATH) > 0 and
        os.path.isfile(COOKIE_PATH)
    )

def get_cookie_info() -> str:
    if cookie_active():
        size     = os.path.getsize(COOKIE_PATH)
        modified = datetime.fromtimestamp(os.path.getmtime(COOKIE_PATH))
        return (
            f"🍪 **Cookie Active**\n"
            f"📄 Size: {human_size(size)}\n"
            f"🕐 Modified: {modified.strftime('%Y-%m-%d %H:%M')}"
        )
    return "❌ **No Cookie Set**\nSend a Netscape cookie file and reply with /setcookie"

def clean_filename(filename: str) -> str:
    for ch in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
        filename = filename.replace(ch, '_')
    if len(filename) > 100:
        filename = filename[:97] + "..."
    return filename.strip()

# ─────────────────────────────────────────────────────────────
# YOUTUBE DL OPTIONS
# ─────────────────────────────────────────────────────────────

def get_base_ydl_opts() -> Dict[str, Any]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "ignoreerrors": False,
        "no_color": True,
        "extractor_retries": 3,
        "fragment_retries": 3,
        "skip_unavailable_fragments": True,
        "geo_bypass": True,
        "geo_bypass_country": "US",
    }
    if cookie_active():
        opts["cookiefile"] = COOKIE_PATH
        opts["cookiesfrombrowser"] = None
    return opts

def get_video_info(url: str) -> Optional[Dict[str, Any]]:
    opts = get_base_ydl_opts()
    opts["skip_download"]    = True
    opts["dump_single_json"] = True
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        print(f"Error fetching video info: {e}")
        return None

def _make_progress_hook(callback, start_time):
    def hook(d):
        if d["status"] == "downloading":
            downloaded = d.get("downloaded_bytes", 0)
            total      = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            pct        = (downloaded / total * 100) if total else 0
            if callback:
                callback({
                    "status":           "downloading",
                    "percentage":       pct,
                    "downloaded_bytes": downloaded,
                    "total_bytes":      total,
                    "speed":            d.get("speed", 0),
                    "eta":              d.get("eta", 0),
                    "elapsed":          time.time() - start_time,
                })
        elif d["status"] == "finished":
            if callback:
                callback({
                    "status":   "finished",
                    "filename": d.get("filename"),
                    "elapsed":  time.time() - start_time,
                })
    return hook

def download_video(url: str, format_spec: str, output_path: str,
                   progress_callback=None) -> Dict[str, Any]:
    start = time.time()
    opts  = get_base_ydl_opts()
    opts["outtmpl"]            = os.path.join(output_path, "%(title)s.%(ext)s")
    opts["progress_hooks"]     = [_make_progress_hook(progress_callback, start)]
    opts["format"]             = format_spec
    opts["merge_output_format"] = "mp4"
    # Prefer mp4/m4a but fall back gracefully to any available container
    opts["format_sort"] = ["res", "ext:mp4:m4a", "tbr", "vbr", "abr"]

    result = {"success": False, "filename": None, "title": None, "error": None, "file_size": 0}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            result["title"] = info.get("title", "Unknown")
            filename = ydl.prepare_filename(info)
            # Might be .mp4 after merge
            for candidate in [filename, os.path.splitext(filename)[0] + ".mp4"]:
                if os.path.exists(candidate):
                    result["filename"]  = candidate
                    result["file_size"] = os.path.getsize(candidate)
                    result["success"]   = True
                    break
            if not result["success"]:
                files = list(Path(output_path).glob("*.mp4"))
                if not files:
                    files = list(Path(output_path).glob("*"))
                if files:
                    result["filename"]  = str(files[0])
                    result["file_size"] = os.path.getsize(result["filename"])
                    result["success"]   = True
    except Exception as e:
        result["error"] = str(e)
        print(f"Download error: {e}")
    return result

def download_audio(url: str, quality: str, output_path: str,
                   progress_callback=None) -> Dict[str, Any]:
    quality_map = {"320": 320, "256": 256, "192": 192, "128": 128}
    abr   = quality_map.get(quality, 192)
    start = time.time()
    opts  = get_base_ydl_opts()
    opts["outtmpl"]        = os.path.join(output_path, "%(title)s.%(ext)s")
    opts["progress_hooks"] = [_make_progress_hook(progress_callback, start)]
    opts["format"]         = "bestaudio/best"
    opts["postprocessors"] = [{
        "key":              "FFmpegExtractAudio",
        "preferredcodec":   "mp3",
        "preferredquality": str(abr),
    }]
    opts["writethumbnail"] = False

    result = {"success": False, "filename": None, "title": None, "error": None, "file_size": 0}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            result["title"] = info.get("title", "Unknown")
            mp3 = os.path.splitext(ydl.prepare_filename(info))[0] + ".mp3"
            if os.path.exists(mp3):
                result.update(filename=mp3, file_size=os.path.getsize(mp3), success=True)
            else:
                files = list(Path(output_path).glob("*.mp3"))
                if files:
                    result.update(
                        filename=str(files[0]),
                        file_size=os.path.getsize(str(files[0])),
                        success=True,
                    )
    except Exception as e:
        result["error"] = str(e)
        print(f"Audio download error: {e}")
    return result

# ─────────────────────────────────────────────────────────────
# KEYBOARD GENERATORS
# ─────────────────────────────────────────────────────────────

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Download Video", callback_data="menu_video"),
            InlineKeyboardButton("🎵 Download MP3",   callback_data="menu_audio"),
        ],
        [
            InlineKeyboardButton("🍪 Cookie Settings", callback_data="menu_cookie"),
            InlineKeyboardButton("❓ Help",             callback_data="menu_help"),
        ],
    ])

def get_video_quality_keyboard(video_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 Best (Auto)",  callback_data=f"video_best|{video_id}"),
            InlineKeyboardButton("📺 1080p HD",      callback_data=f"video_1080|{video_id}"),
        ],
        [
            InlineKeyboardButton("📺 720p HD",  callback_data=f"video_720|{video_id}"),
            InlineKeyboardButton("📺 480p SD",  callback_data=f"video_480|{video_id}"),
        ],
        [
            InlineKeyboardButton("📺 360p SD",  callback_data=f"video_360|{video_id}"),
            InlineKeyboardButton("📺 240p SD",  callback_data=f"video_240|{video_id}"),
        ],
        [
            InlineKeyboardButton("🔙 Back",   callback_data="menu_video"),
            InlineKeyboardButton("❌ Cancel", callback_data="menu_cancel"),
        ],
    ])

def get_audio_quality_keyboard(audio_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎧 320 kbps (Best)", callback_data=f"audio_320|{audio_id}"),
            InlineKeyboardButton("🎧 256 kbps",        callback_data=f"audio_256|{audio_id}"),
        ],
        [
            InlineKeyboardButton("🎧 192 kbps", callback_data=f"audio_192|{audio_id}"),
            InlineKeyboardButton("🎧 128 kbps", callback_data=f"audio_128|{audio_id}"),
        ],
        [
            InlineKeyboardButton("🔙 Back",   callback_data="menu_audio"),
            InlineKeyboardButton("❌ Cancel", callback_data="menu_cancel"),
        ],
    ])

def get_cookie_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 Set Cookie",    callback_data="cookie_set"),
            InlineKeyboardButton("🗑️ Delete Cookie", callback_data="cookie_delete"),
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="menu_main")],
    ])

def get_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")],
    ])

# ─────────────────────────────────────────────────────────────
# DOWNLOAD STATE MANAGEMENT
# ─────────────────────────────────────────────────────────────

class DownloadState:
    def __init__(self, user_id: int, chat_id: int, message_id: int):
        self.user_id       = user_id
        self.chat_id       = chat_id
        self.message_id    = message_id
        self.status        = "pending"
        self.progress      = 0.0
        self.speed         = 0
        self.eta           = 0
        self.downloaded_bytes = 0
        self.total_bytes   = 0
        self.elapsed       = 0.0
        self.filename      = None
        self.title         = None
        self.error         = None
        self.start_time    = time.time()
        self.url           = None
        self.format_spec   = None
        self.download_type = None   # "video" | "audio"
        self.quality       = None
        self.cancelled     = False

download_states: Dict[str, DownloadState] = {}
# Temporary store for URL pending quality selection  {user_id: url}
pending_urls: Dict[int, Dict[str, str]] = {}

def create_download_state(user_id: int, chat_id: int, message_id: int) -> str:
    sid = f"{user_id}_{chat_id}_{message_id}_{int(time.time())}"
    download_states[sid] = DownloadState(user_id, chat_id, message_id)
    return sid

def get_download_state(sid: str) -> Optional[DownloadState]:
    return download_states.get(sid)

def update_download_progress(sid: str, data: Dict[str, Any]):
    state = download_states.get(sid)
    if not state:
        return
    if data["status"] == "downloading":
        state.status           = "downloading"
        state.progress         = data.get("percentage", 0)
        state.speed            = data.get("speed", 0)
        state.eta              = data.get("eta", 0)
        state.downloaded_bytes = data.get("downloaded_bytes", 0)
        state.total_bytes      = data.get("total_bytes", 0)
        state.elapsed          = data.get("elapsed", 0)
    elif data["status"] == "finished":
        state.status  = "merging"
        state.elapsed = data.get("elapsed", 0)

def build_progress_text(state: DownloadState) -> str:
    bar = progress_bar(state.progress)
    dl  = human_size(state.downloaded_bytes)
    tot = human_size(state.total_bytes) if state.total_bytes else "?"
    spd = f"{human_size(int(state.speed))}/s" if state.speed else "?"
    eta = human_time(state.eta) if state.eta else "?"
    title = f"**{state.title[:50]}**\n" if state.title else ""
    return (
        f"⬇️ Downloading…\n"
        f"{title}"
        f"{bar} `{state.progress:.1f}%`\n"
        f"📦 {dl} / {tot}\n"
        f"⚡ Speed: {spd}\n"
        f"⏱ ETA: {eta}"
    )

# ─────────────────────────────────────────────────────────────
# BOT CLIENT
# ─────────────────────────────────────────────────────────────

app = Client(
    "ytdl_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# ─────────────────────────────────────────────────────────────
# COMMAND HANDLERS
# ─────────────────────────────────────────────────────────────

HELP_TEXT = (
    "**🤖 YouTube Downloader Bot**\n\n"
    "**Commands:**\n"
    "/start — Start the bot\n"
    "/help  — Show this message\n"
    "/mp4 `<url>` — Download as video (MP4)\n"
    "/mp3 `<url>` — Download as audio (MP3)\n"
    "/dl  `<url>` — Choose format interactively\n"
    "/cookie    — Show cookie status\n"
    "/setcookie — Reply to a .txt cookie file\n"
    "/delcookie — Remove saved cookie\n\n"
    "**Tips:**\n"
    "• Works with YouTube, YouTube Music, Shorts\n"
    "• Use cookies to bypass bot-detection\n"
    "• Max file size: 2 GB · Max duration: 2 h"
)

@app.on_message(filters.command("start"))
async def cmd_start(client: Client, message: Message):
    await message.reply_text(
        f"👋 Hello **{message.from_user.first_name}**!\n\n"
        "I can download YouTube videos and audio for you.\n"
        "Send me a URL or use the menu below 👇",
        reply_markup=get_main_keyboard(),
    )

@app.on_message(filters.command("help"))
async def cmd_help(client: Client, message: Message):
    await message.reply_text(HELP_TEXT, reply_markup=get_main_keyboard())

@app.on_message(filters.command("cookie"))
async def cmd_cookie(client: Client, message: Message):
    await message.reply_text(get_cookie_info(), reply_markup=get_cookie_keyboard())

@app.on_message(filters.command("delcookie"))
async def cmd_delcookie(client: Client, message: Message):
    if os.path.exists(COOKIE_PATH):
        os.remove(COOKIE_PATH)
        await message.reply_text("🗑️ Cookie deleted successfully!")
    else:
        await message.reply_text("❌ No cookie file found.")

@app.on_message(filters.command("setcookie"))
async def cmd_setcookie(client: Client, message: Message):
    replied = message.reply_to_message
    if not replied or not replied.document:
        await message.reply_text(
            "📎 Please **reply to a cookie .txt file** with /setcookie\n\n"
            "Export cookies in Netscape format using a browser extension like "
            "'cookies.txt' or 'Get cookies.txt'."
        )
        return
    doc = replied.document
    if not (doc.file_name or "").endswith(".txt") and "text" not in (doc.mime_type or ""):
        await message.reply_text("❌ Please send a `.txt` cookie file.")
        return
    status = await message.reply_text("⬇️ Downloading cookie file…")
    try:
        await replied.download(file_name=COOKIE_PATH)
        await status.edit_text(
            f"✅ Cookie saved!\n\n{get_cookie_info()}",
            reply_markup=get_cookie_keyboard(),
        )
    except Exception as e:
        await status.edit_text(f"❌ Failed to save cookie: `{e}`")

# ─────────────────────────────────────────────────────────────
# DOWNLOAD COMMANDS  (/mp4, /mp3, /dl)
# ─────────────────────────────────────────────────────────────

async def _ask_video_quality(message: Message, url: str):
    user_id = message.from_user.id
    pending_urls.setdefault(user_id, {})["video"] = url
    status = await message.reply_text("🔍 Fetching video info…")
    info   = await asyncio.to_thread(get_video_info, url)
    if not info:
        await status.edit_text("❌ Could not fetch video info. Check the URL.")
        return
    title    = info.get("title", "Unknown")
    duration = info.get("duration", 0)
    if duration and duration > MAX_DURATION:
        await status.edit_text(
            f"⚠️ Video too long ({human_time(duration)}). Max: {human_time(MAX_DURATION)}."
        )
        return
    thumb = info.get("thumbnail", "")
    text  = (
        f"🎬 **{title[:80]}**\n"
        f"⏱ Duration: {human_time(duration)}\n"
        f"👤 Channel: {info.get('uploader','?')}\n\n"
        "Select video quality:"
    )
    vid_id = f"v_{user_id}_{int(time.time())}"
    pending_urls[user_id]["vid_id"] = vid_id
    await status.edit_text(text, reply_markup=get_video_quality_keyboard(vid_id))

async def _ask_audio_quality(message: Message, url: str):
    user_id = message.from_user.id
    pending_urls.setdefault(user_id, {})["audio"] = url
    status = await message.reply_text("🔍 Fetching video info…")
    info   = await asyncio.to_thread(get_video_info, url)
    if not info:
        await status.edit_text("❌ Could not fetch video info. Check the URL.")
        return
    title    = info.get("title", "Unknown")
    duration = info.get("duration", 0)
    if duration and duration > MAX_DURATION:
        await status.edit_text(
            f"⚠️ Video too long ({human_time(duration)}). Max: {human_time(MAX_DURATION)}."
        )
        return
    text   = (
        f"🎵 **{title[:80]}**\n"
        f"⏱ Duration: {human_time(duration)}\n\n"
        "Select audio quality:"
    )
    aud_id = f"a_{user_id}_{int(time.time())}"
    pending_urls[user_id]["aud_id"] = aud_id
    await status.edit_text(text, reply_markup=get_audio_quality_keyboard(aud_id))

@app.on_message(filters.command("mp4"))
async def cmd_mp4(client: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("Usage: `/mp4 <YouTube URL>`")
        return
    await _ask_video_quality(message, parts[1].strip())

@app.on_message(filters.command("mp3"))
async def cmd_mp3(client: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("Usage: `/mp3 <YouTube URL>`")
        return
    await _ask_audio_quality(message, parts[1].strip())

@app.on_message(filters.command("dl"))
async def cmd_dl(client: Client, message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text(
            "Usage: `/dl <YouTube URL>`\n\nOr tap a button below:",
            reply_markup=get_main_keyboard(),
        )
        return
    url     = parts[1].strip()
    user_id = message.from_user.id
    pending_urls.setdefault(user_id, {})["last_url"] = url
    await message.reply_text(
        f"🔗 URL saved!\nChoose format:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎬 Video (MP4)", callback_data=f"dl_video|{user_id}"),
                InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"dl_audio|{user_id}"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu_cancel")],
        ]),
    )

# ─────────────────────────────────────────────────────────────
# CORE DOWNLOAD WORKER
# ─────────────────────────────────────────────────────────────

async def run_download(
    client: Client,
    chat_id: int,
    user_id: int,
    status_msg: Message,
    url: str,
    dl_type: str,       # "video" | "audio"
    quality: str,       # "best"|"1080"|"720"|... or "320"|"256"|"192"|"128"
):
    sid = create_download_state(user_id, chat_id, status_msg.id)
    state = download_states[sid]
    state.url           = url
    state.download_type = dl_type
    state.quality       = quality

    tmpdir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)

    try:
        last_edit = [0.0]   # mutable for closure

        def progress_cb(data):
            update_download_progress(sid, data)

        async def edit_progress_loop():
            while state.status in ("pending", "downloading", "merging"):
                now = time.time()
                if now - last_edit[0] >= 3:
                    try:
                        if state.status in ("downloading",):
                            await status_msg.edit_text(build_progress_text(state))
                        elif state.status == "merging":
                            await status_msg.edit_text("🔀 Merging streams… please wait.")
                        last_edit[0] = now
                    except Exception:
                        pass
                await asyncio.sleep(1)

        progress_task = asyncio.create_task(edit_progress_loop())

        # Map quality to format spec — each line has multiple fallbacks
        if dl_type == "video":
            quality_map = {
                "best": (
                    "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                    "/bestvideo+bestaudio"
                    "/best[ext=mp4]"
                    "/best"
                ),
                "1080": (
                    "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
                    "/bestvideo[height<=1080]+bestaudio"
                    "/best[height<=1080][ext=mp4]"
                    "/best[height<=1080]"
                    "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                    "/bestvideo+bestaudio"
                    "/best"
                ),
                "720": (
                    "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]"
                    "/bestvideo[height<=720]+bestaudio"
                    "/best[height<=720][ext=mp4]"
                    "/best[height<=720]"
                    "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                    "/bestvideo+bestaudio"
                    "/best"
                ),
                "480": (
                    "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]"
                    "/bestvideo[height<=480]+bestaudio"
                    "/best[height<=480][ext=mp4]"
                    "/best[height<=480]"
                    "/bestvideo+bestaudio"
                    "/best"
                ),
                "360": (
                    "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]"
                    "/bestvideo[height<=360]+bestaudio"
                    "/best[height<=360]"
                    "/bestvideo+bestaudio"
                    "/best"
                ),
                "240": (
                    "bestvideo[height<=240][ext=mp4]+bestaudio[ext=m4a]"
                    "/bestvideo[height<=240]+bestaudio"
                    "/best[height<=240]"
                    "/bestvideo+bestaudio"
                    "/best"
                ),
            }
            fmt = quality_map.get(quality, quality_map["best"])
            result = await asyncio.to_thread(download_video, url, fmt, tmpdir, progress_cb)
        else:
            result = await asyncio.to_thread(download_audio, url, quality, tmpdir, progress_cb)

        progress_task.cancel()

        if not result["success"]:
            await status_msg.edit_text(
                f"❌ Download failed!\n`{result.get('error', 'Unknown error')}`",
                reply_markup=get_back_keyboard(),
            )
            return

        file_path = result["filename"]
        file_size = result["file_size"]
        title     = result.get("title", "Unknown")
        state.title = title

        if file_size > MAX_TG_SIZE:
            await status_msg.edit_text(
                f"❌ File too large: {human_size(file_size)}\n"
                f"Telegram limit is {human_size(MAX_TG_SIZE)}.",
                reply_markup=get_back_keyboard(),
            )
            return

        await status_msg.edit_text(
            f"📤 Uploading **{title[:60]}**…\n"
            f"📦 Size: {human_size(file_size)}"
        )

        caption = (
            f"**{title}**\n"
            f"📦 {human_size(file_size)}"
        )

        if dl_type == "audio":
            await client.send_audio(
                chat_id=chat_id,
                audio=file_path,
                caption=caption,
                title=title[:64],
            )
        else:
            await client.send_video(
                chat_id=chat_id,
                video=file_path,
                caption=caption,
                supports_streaming=True,
            )

        await status_msg.delete()

    except asyncio.CancelledError:
        pass
    except Exception as e:
        await status_msg.edit_text(
            f"❌ Unexpected error: `{e}`",
            reply_markup=get_back_keyboard(),
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        download_states.pop(sid, None)

# ─────────────────────────────────────────────────────────────
# CALLBACK QUERY HANDLER
# ─────────────────────────────────────────────────────────────

@app.on_callback_query()
async def handle_callback(client: Client, query: CallbackQuery):
    data    = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    msg     = query.message

    await query.answer()

    # ── Main menu ──────────────────────────────────────────────
    if data == "menu_main":
        await msg.edit_text("👋 Main Menu:", reply_markup=get_main_keyboard())
        return

    if data == "menu_cancel":
        await msg.edit_text("❌ Cancelled.", reply_markup=get_back_keyboard())
        return

    if data == "menu_help":
        await msg.edit_text(HELP_TEXT, reply_markup=get_back_keyboard())
        return

    if data == "menu_cookie":
        await msg.edit_text(get_cookie_info(), reply_markup=get_cookie_keyboard())
        return

    if data == "cookie_delete":
        if os.path.exists(COOKIE_PATH):
            os.remove(COOKIE_PATH)
            await msg.edit_text("🗑️ Cookie deleted!", reply_markup=get_cookie_keyboard())
        else:
            await msg.edit_text("❌ No cookie found.", reply_markup=get_cookie_keyboard())
        return

    if data == "cookie_set":
        await msg.edit_text(
            "📎 Send a Netscape cookie `.txt` file, then reply to it with `/setcookie`.",
            reply_markup=get_back_keyboard(),
        )
        return

    if data == "menu_video":
        await msg.edit_text(
            "🎬 **Video Download**\n\nSend a YouTube URL with `/mp4 <url>`\nor paste a URL and I'll detect it automatically.",
            reply_markup=get_back_keyboard(),
        )
        return

    if data == "menu_audio":
        await msg.edit_text(
            "🎵 **Audio Download**\n\nSend a YouTube URL with `/mp3 <url>`\nor paste a URL and I'll detect it automatically.",
            reply_markup=get_back_keyboard(),
        )
        return

    # ── dl_ format picker (from /dl command) ──────────────────
    if data.startswith("dl_video|") or data.startswith("dl_audio|"):
        _, uid_str = data.split("|", 1)
        uid  = int(uid_str)
        umap = pending_urls.get(uid, {})
        url  = umap.get("last_url")
        if not url:
            await msg.edit_text("❌ URL not found. Please use `/dl <url>` again.")
            return
        fake_msg = msg   # reuse the existing message
        if data.startswith("dl_video|"):
            vid_id = f"v_{uid}_{int(time.time())}"
            pending_urls.setdefault(uid, {})["video"] = url
            pending_urls[uid]["vid_id"] = vid_id
            await msg.edit_text("Select video quality:", reply_markup=get_video_quality_keyboard(vid_id))
        else:
            aud_id = f"a_{uid}_{int(time.time())}"
            pending_urls.setdefault(uid, {})["audio"] = url
            pending_urls[uid]["aud_id"] = aud_id
            await msg.edit_text("Select audio quality:", reply_markup=get_audio_quality_keyboard(aud_id))
        return

    # ── video quality selected ─────────────────────────────────
    if data.startswith("video_"):
        parts   = data.split("|", 1)
        quality = parts[0].replace("video_", "")   # best / 1080 / 720 …
        vid_id  = parts[1] if len(parts) > 1 else ""
        umap    = pending_urls.get(user_id, {})
        url     = umap.get("video")
        if not url:
            await msg.edit_text("❌ Session expired. Please send the URL again.")
            return
        await msg.edit_text("⏳ Starting download…")
        asyncio.create_task(run_download(client, chat_id, user_id, msg, url, "video", quality))
        return

    # ── audio quality selected ─────────────────────────────────
    if data.startswith("audio_"):
        parts   = data.split("|", 1)
        quality = parts[0].replace("audio_", "")   # 320 / 256 / 192 / 128
        aud_id  = parts[1] if len(parts) > 1 else ""
        umap    = pending_urls.get(user_id, {})
        url     = umap.get("audio")
        if not url:
            await msg.edit_text("❌ Session expired. Please send the URL again.")
            return
        await msg.edit_text("⏳ Starting download…")
        asyncio.create_task(run_download(client, chat_id, user_id, msg, url, "audio", quality))
        return

    # Fallback
    await query.answer("Unknown action.", show_alert=True)

# ─────────────────────────────────────────────────────────────
# AUTO-DETECT URLs IN PLAIN MESSAGES
# ─────────────────────────────────────────────────────────────

YT_DOMAINS = ("youtube.com/watch", "youtu.be/", "youtube.com/shorts/",
              "music.youtube.com/")

@app.on_message(filters.text & ~filters.command(["start","help","dl","mp3","mp4","cookie","setcookie","delcookie"]))
async def auto_detect_url(client: Client, message: Message):
    text = message.text or ""
    url  = None
    for token in text.split():
        if any(d in token for d in YT_DOMAINS):
            url = token
            break
    if not url:
        return
    user_id = message.from_user.id
    pending_urls.setdefault(user_id, {})["last_url"] = url
    await message.reply_text(
        f"🔗 YouTube URL detected!\nChoose format:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎬 Video (MP4)", callback_data=f"dl_video|{user_id}"),
                InlineKeyboardButton("🎵 Audio (MP3)", callback_data=f"dl_audio|{user_id}"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu_cancel")],
        ]),
    )

# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🚀 Starting YouTube Downloader Bot…")
    app.run()
