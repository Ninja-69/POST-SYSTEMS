import os
import time
import shutil
import random
import hashlib
import concurrent.futures
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai
from PIL import Image, ImageFont, ImageDraw
from pilmoji import Pilmoji
from pilmoji.source import AppleEmojiSource
import telebot
from pymongo import MongoClient
import subprocess

load_dotenv()

# --- CONFIG ---
RAW_DIR = Path("1_raw_input")
PRODUCTION_DIR = Path("2_in_production")
READY_DIR = Path("3_ready_to_upload")
WAREHOUSE_DIR = Path("4_cloud_warehouse")
FAILED_DIR = Path("5_failed_logs")
FONTS_DIR = Path("fonts")
EXAMPLES_FILE = Path("examples.txt")

for d in [RAW_DIR, PRODUCTION_DIR, READY_DIR, WAREHOUSE_DIR, FAILED_DIR, FONTS_DIR]:
    d.mkdir(exist_ok=True)
(WAREHOUSE_DIR / "raw_backups").mkdir(exist_ok=True)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_DATABASE_CHANNEL_ID")
MONGO_URI = os.getenv("MONGO_URI")
keys_str = os.getenv("GEMINI_API_KEYS", "")
GEMINI_KEYS = [k.strip() for k in keys_str.split(",") if k.strip()]

bot = telebot.TeleBot(TOKEN) if TOKEN else None
db = MongoClient(MONGO_URI)["fundedai_factory"]["video_queue"] if MONGO_URI else None
API_KEY_COOLDOWN_MAP = {}

# --- CORE UTILS ---
def get_video_hash(file_path):
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        buf = f.read(5242880)
        hasher.update(buf)
    return hasher.hexdigest()

def is_duplicate(v_hash):
    if not db: return False
    return db.find_one({"video_hash": v_hash}) is not None

def get_current_trends():
    trends_file = Path("trends.txt")
    if trends_file.exists():
        with open(trends_file, "r", encoding="utf-8") as f: return f.read().strip()
    return "No trends."

def analyze_video(video_path):
    now = time.time()
    available_keys = [k for k in GEMINI_KEYS if API_KEY_COOLDOWN_MAP.get(k, 0) < now]
    if not available_keys: return None
    
    key = random.choice(available_keys)
    genai.configure(api_key=key)
    try:
        vf = genai.upload_file(path=video_path)
        while vf.state.name == "PROCESSING": time.sleep(2); vf = genai.get_file(vf.name)
        model = genai.GenerativeModel("models/gemini-flash-latest")
        # (Prompt logic remains same as in main.py but simplified for export)
        prompt = f"Analyze this trading video. Current Trends: {get_current_trends()}. Output Hook | CTA \n SEO Caption"
        res = model.generate_content([vf, prompt])
        vf.update_file_state() # Just a sync
        vf.delete()
        return res.text.strip()
    except Exception as e:
        if "429" in str(e): API_KEY_COOLDOWN_MAP[key] = time.time() + 3600
        return None

# --- FACTORY ACTIONS ---
def action_release(log_func=print):
    video_exts = ["*.mp4", "*.MOV", "*.mov", "*.MP4"]
    ready_videos = []
    for ext in video_exts: ready_videos.extend(list(READY_DIR.glob(ext)))
    
    count = 0
    for v in ready_videos:
        t = READY_DIR / f"{v.stem}.txt"
        if t.exists() and bot and db:
            attempts = 0
            while attempts < 3:
                try:
                    with open(t, "r", encoding="utf-8") as f: 
                        cap = f.read().strip()
                    
                    if not cap:
                        log_func(f"⚠️ Skipping {v.name}: Caption file is empty.")
                        break
                        
                    with open(v, "rb") as vf: 
                        # Send video with caption as ONE message to the storage channel
                        msg = bot.send_video(CHANNEL_ID, vf, caption=f"🎥 {v.name}\n\n{cap}")
                    
                    # Save message_id AND the specific file_id for faster re-sending
                    video_file_id = msg.video.file_id
                    
                    db.update_one(
                        {"video_name": v.name}, 
                        {"$set": {
                            "message_id": msg.message_id, 
                            "video_file_id": video_file_id,
                            "caption": cap,
                            "status": "unposted",
                            "timestamp": time.time()
                        }}, 
                        upsert=True
                    )
                    shutil.move(str(v), str(WAREHOUSE_DIR / v.name))
                    shutil.move(str(t), str(WAREHOUSE_DIR / t.name))
                    log_func(f"✅ Released: {v.name}")
                    count += 1
                    
                    # Mandatory 3-second cooldown to stay under Telegram's radar
                    time.sleep(3)
                    break 
                    
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str:
                        # Extract wait time from Telegram error
                        import re
                        wait_match = re.search(r"retry after (\d+)", err_str)
                        wait_seconds = int(wait_match.group(1)) if wait_match else 30
                        log_func(f"⏳ Rate Limited! Sleeping for {wait_seconds}s before retry...")
                        time.sleep(wait_seconds + 2)
                        attempts += 1
                    else:
                        log_func(f"❌ Error releasing {v.name}: {e}")
                        break
    return count

# --- REMOTE DOWNLOADING ---
DOWNLOAD_ZONE = Path("0_download_zone")
DOWNLOAD_ZONE.mkdir(exist_ok=True)

def action_download(dl_type, link, log_func=print):
    import traceback, threading, time
    log_func(f"⏳ INITIALIZING: {dl_type} remote link...")
    
    dl_type = dl_type.lower()
    stop_pulse = False
    
    last_size = 0
    def pulse():
        nonlocal last_size
        while not stop_pulse:
            try:
                files = list(DOWNLOAD_ZONE.glob("*.*"))
                total_size = sum(f.stat().st_size for f in files) / (1024 * 1024)
                
                # Calculate Speed
                if last_size > 0:
                    speed = (total_size - last_size) / 5 # Because sleep is 5
                    log_func(f"📊 PROGRESS: {len(files)} files ({total_size:.1f} MB) | Speed: {speed:.2f} MB/s")
                else:
                    log_func(f"📊 PROGRESS: {len(files)} files ({total_size:.1f} MB) | Initializing...")
                
                last_size = total_size
            except: pass
            time.sleep(5)

    try:
        pulse_thread = threading.Thread(target=pulse, daemon=True)
        pulse_thread.start()
        
        if dl_type in ["gdrive", "drive", "google"]:
            import gdown, re, os
            
            # Extract Folder ID
            match = re.search(r"folders/([a-zA-Z0-9_-]+)", link)
            folder_id = match.group(1) if match else link
            
            log_func(f"📡 Surgical Mode: Listing files for ID {folder_id}...")
            
            try:
                # 1. Get the list of files without downloading
                files_to_dl = gdown.download_folder(id=folder_id, output=str(DOWNLOAD_ZONE), quiet=True, skip_download=True)
                
                if not files_to_dl:
                    log_func("❌ No files found in folder or access denied.")
                    return False
                
                log_func(f"📦 Found {len(files_to_dl)} files. Starting individual pulls...")
                
                success_count = 0
                skip_count = 0
                for i, f_info in enumerate(files_to_dl):
                    f_name = os.path.basename(f_info.path)
                    f_id = f_info.id
                    dest = os.path.join(DOWNLOAD_ZONE, f_name)
                    
                    if os.path.exists(dest):
                        skip_count += 1
                        success_count += 1
                        # We only log every 50 skips to avoid Telegram 429 errors
                        if skip_count % 50 == 0:
                            log_func(f"⏩ Verified {skip_count} existing files so far...")
                        continue
                    
                    log_func(f"📥 [{i+1}/{len(files_to_dl)}] Pulling: {f_name}...")
                    try:
                        # Individual file download - no extra flags to avoid errors
                        gdown.download(id=f_id, output=dest, quiet=True)
                        success_count += 1
                    except Exception as e:
                        log_func(f"⚠️ Failed to pull {f_name}: {str(e)[:100]}")
                
                log_func(f"✅ Surgical Session Complete: {success_count}/{len(files_to_dl)} files secured.")
                return True
                
            except Exception as e:
                log_func(f"‼️ Surgical Error: {str(e)}")
                return False

            stop_pulse = True
            if output:
                log_func(f"✅ GDrive folder download complete: {len(output)} files.")
                return True
            else:
                log_func("❌ GDrive Error: No files were returned. Check link permissions.")
                return False
                
        elif dl_type == "mega":
            from mega import Mega
            mega = Mega()
            log_func("📡 Connecting to MEGA Cloud...")
            m = mega.login()
            m.download_url(link, dest_path=str(DOWNLOAD_ZONE))
            stop_pulse = True
            log_func("✅ MEGA download complete.")
            return True
            
    except Exception as e:
        stop_pulse = True
        error_msg = f"‼️ CRITICAL ERROR during {dl_type} download:\n{str(e)}\n\n{traceback.format_exc()[:300]}"
        log_func(error_msg)
        return False
    finally:
        stop_pulse = True

def action_import(log_func=print):
    count = 0
    for f in DOWNLOAD_ZONE.glob("*.*"):
        if f.suffix.lower() in [".mp4", ".mov", ".avi", ".mkv"]:
            shutil.move(str(f), str(RAW_DIR / f.name))
            log_func(f"📦 Imported: {f.name}")
            count += 1
    return count
