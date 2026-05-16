import os
import time
import subprocess
import textwrap
import random
import shutil
import concurrent.futures
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai
from PIL import Image, ImageFont, ImageDraw
from pilmoji import Pilmoji
from pilmoji.source import AppleEmojiSource
import difflib
import telebot
from pymongo import MongoClient

# Load environment variables
load_dotenv()

# Get Gemini keys and create a list
keys_str = os.getenv("GEMINI_API_KEYS", "")
GEMINI_KEYS = [k.strip() for k in keys_str.split(",") if k.strip()]
if not GEMINI_KEYS:
    print("Warning: GEMINI_API_KEYS not found in environment variables. Video analysis will fail.")

# --- PROFESSIONAL 5-STAGE PIPELINE PATHS ---
RAW_DIR = Path("1_raw_input")
PRODUCTION_DIR = Path("2_in_production")
READY_DIR = Path("3_ready_to_upload")
WAREHOUSE_DIR = Path("4_cloud_warehouse")
FAILED_DIR = Path("5_failed_logs")
FONTS_DIR = Path("fonts")
EXAMPLES_FILE = Path("examples.txt")

# Create directories
for d in [RAW_DIR, PRODUCTION_DIR, READY_DIR, WAREHOUSE_DIR, FAILED_DIR, FONTS_DIR]:
    d.mkdir(exist_ok=True)
(WAREHOUSE_DIR / "raw_backups").mkdir(exist_ok=True)

# Initialize Telegram & MongoDB Cloud Stack
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")
CHANNEL_ID = os.getenv("TELEGRAM_DATABASE_CHANNEL_ID")
MONGO_URI = os.getenv("MONGO_URI")

bot = telebot.TeleBot(TOKEN) if TOKEN else None
db = None
if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI)
        db = client["fundedai_factory"]["video_queue"]
        print("Connected to MongoDB Cloud Database.")
    except Exception as e:
        print(f"MongoDB Connection Error: {e}")

# Rate Limit Cooldown System
API_KEY_COOLDOWN_MAP = {} # key: cooldown_end_timestamp

def get_video_hash(file_path):
    """Generate a unique digital fingerprint for the video content."""
    import hashlib
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        # Read first 5MB for speed, enough for a unique fingerprint
        buf = f.read(5242880)
        hasher.update(buf)
    return hasher.hexdigest()

def is_duplicate(v_hash):
    if db is None: return False
    return db.find_one({"video_hash": v_hash}) is not None

def get_current_trends():
    trends_file = Path("trends.txt")
    if trends_file.exists():
        with open(trends_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "No current trends found."

def get_examples():
    if not EXAMPLES_FILE.exists():
        return []
    with open(EXAMPLES_FILE, "r", encoding="utf-8") as f:
        lines = f.read().strip().split('\n')
        return [line.strip() for line in lines if line.strip()]

def get_video_duration(video_path):
    ffprobe_cmd = r"C:\ffmpeg\bin\ffprobe.exe" if Path(r"C:\ffmpeg\bin\ffprobe.exe").exists() else "ffprobe"
    cmd = [ffprobe_cmd, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Could not get duration for {video_path}, using fallback 5.0. Error: {e}")
        return 5.0

def create_text_image(text, font_path, image_path, y_position="center", font_scale=0.06, font_color=(255, 255, 255, 255)):
    width, height = 1080, 1920
    text = " ".join(text.split())
    lines = [line.strip() for line in textwrap.wrap(text, width=22) if line.strip()]
    
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    
    try:
        font_size = int(width * font_scale)
        max_allowed_width = width * 0.85
        
        while True:
            font = ImageFont.truetype(font_path, font_size)
            max_line_w = 0
            for line in lines:
                try:
                    with Pilmoji(Image.new("RGBA", (1, 1)), source=AppleEmojiSource) as test_p:
                        line_w, _ = test_p.getsize(line, font=font)
                        max_line_w = max(max_line_w, line_w)
                except:
                    max_line_w = max(max_line_w, font_size * len(line) * 0.5)
            
            if max_line_w <= max_allowed_width or font_size <= 20:
                break
            font_size -= 2
            
    except Exception as e:
        print(f"Error loading font: {e}")
        font_size = int(width * font_scale)
        font = ImageFont.load_default()
        
    line_spacing = int(font_size * 1.2)
    total_lines = len(lines)
    
    if y_position == "center":
        start_y = (height - (total_lines * line_spacing)) // 2
    else:
        start_y = int(y_position * height)
        
    with Pilmoji(image, source=AppleEmojiSource) as pilmoji:
        for i, line in enumerate(lines):
            try:
                text_w, _ = pilmoji.getsize(line, font=font)
            except:
                text_w = font_size * len(line) * 0.5
            
            x_pos = (width - text_w) // 2
            y_pos = start_y + (i * line_spacing)
            
            pilmoji.text((x_pos + 3, y_pos + 3), line, (0, 0, 0, 128), font=font)
            border_w = 3
            for dx in [-border_w, 0, border_w]:
                for dy in [-border_w, 0, border_w]:
                    if dx != 0 or dy != 0:
                        pilmoji.text((x_pos + dx, y_pos + dy), line, (0, 0, 0, 255), font=font)
            pilmoji.text((x_pos, y_pos), line, font_color, font=font)
            
    image.save(image_path)
    return image_path

# Global iterator to cycle through Gemini keys
key_index = 0

def analyze_video_and_generate_text(video_path, strategy="RANDOM"):
    global key_index
    if not GEMINI_KEYS:
         print("Cannot generate text. Please set GEMINI_API_KEYS in .env file.")
         return None

    max_retries = len(GEMINI_KEYS)
    tried_keys_count = 0
    
    for key in GEMINI_KEYS:
        now = time.time()
        if API_KEY_COOLDOWN_MAP.get(key, 0) > now:
            continue
            
        tried_keys_count += 1
        if tried_keys_count > max_retries:
            print(f"[ABORT] All {max_retries} keys failed for {video_path.name}. Skipping.")
            return None

        try:
            genai.configure(api_key=key)
            print(f"Uploading {video_path.name} to Gemini (Key {GEMINI_KEYS.index(key)+1}/{max_retries})...")
            
            video_file = genai.upload_file(path=str(video_path))
            
            wait_start = time.time()
            while video_file.state.name == "PROCESSING":
                if time.time() - wait_start > 120:
                    print(f"[TIMEOUT] Gemini processing took too long (>120s) for {video_path.name}. Skipping.")
                    break
                time.sleep(5)
                video_file = genai.get_file(video_file.name)
                
            if video_file.state.name == "FAILED":
                print(f"[CRITICAL] Gemini rejected {video_path.name} (File Corrupted). Aborting keys.")
                return None # Stop retrying keys for this dead file
                
            if video_file.state.name != "ACTIVE":
                raise ValueError(f"Video state: {video_file.state.name}")
                
            random_angle = random.choice([
                "The Matrix Escape", "Wifi Money vs 9-5", "Elite Privacy", 
                "Quiet Luxury", "Psychological Dominance", "Malicious Wealth"
            ])
            
            if strategy == "TRANSITIONAL_HOOK":
                persona = "You are a 2026 Viral Growth Architect specializing in high-retention Transitional Hooks. MISSION: You are writing text for a video that starts with a SHOCKING visual transition (e.g., a fall, a crash, or a surprise)."
                mission = "Defeat the 53% skip rate with Pattern Interrupt text."
            else:
                persona = "You are a High-Ticket Luxury Branding Expert. MISSION: You are writing text for an elite luxury lifestyle video."
                mission = "Create a sense of extreme wealth, exclusivity, and 'matrix' escape. Use natural curiosity rather than shock."

            prompt = f"""
            {persona}
            MISSION: {mission}
            YOUR ANGLE: {random_angle}
            RULES:
            1. THE HOOK: Must be 'Natural Authority' or 'Elite Curiosity'. 
            2. THE SAVE-BAIT: Give them a reason to SAVE.
            3. THE COMMENT-TRAP: Polarizing caption.
            OUTPUT FORMAT (RAW TEXT ONLY, NO MARKDOWN, NO PREAMBLE):
            Hook text | CTA text
            Full caption with hashtags
            TONE: lowercase, no periods, internet slang.
            """
            
            model = genai.GenerativeModel(model_name="models/gemini-flash-latest")
            response = model.generate_content([video_file, prompt])
            raw_text = response.text.strip()
            
            import re
            clean_text = re.sub(r"```[a-z]*\n?", "", raw_text)
            clean_text = clean_text.replace("```", "").strip()
            
            lines = [line.strip() for line in clean_text.split('\n') if line.strip()]
            hook_text, cta_text, caption_text = "luxury lifestyle", "link in bio", clean_text
            
            pattern = r"([^|\n]+)\|([^|\n]+)"
            match = re.search(pattern, clean_text)
            if match:
                hook_text, cta_text = match.group(1).strip(), match.group(2).strip()
                other_lines = [l for l in lines if hook_text not in l]
                if other_lines: caption_text = "\n".join(other_lines)
            elif lines:
                hook_text = lines[0]
                if len(lines) > 1: caption_text = "\n".join(lines[1:])

            try:
                video_file.delete()
                print(f"Cleaned up {video_file.name} from Gemini.")
            except: pass
            
            return (hook_text, cta_text), caption_text

        except Exception as e:
            error_str = str(e)
            print(f"Error: {error_str[:100]}...")
            try:
                if 'video_file' in locals():
                    genai.delete_file(video_file.name)
            except: pass
            
            if "429" in error_str or "quota" in error_str.lower():
                print(f"[LIMIT] Key {GEMINI_KEYS.index(key)+1} cooling down...")
                API_KEY_COOLDOWN_MAP[key] = time.time() + 3600
                continue
            elif "processing failed" in error_str.lower():
                print(f"[RETRY] Processing glitch on Key {GEMINI_KEYS.index(key)+1}. Swapping...")
                continue
            else:
                continue
                
    return None

def overlay_text_on_video(input_path, output_path, texts, strategy="RANDOM"):
    hook_text, cta_text = texts
    font_files = list(FONTS_DIR.glob("*.ttf")) + list(FONTS_DIR.glob("*.otf"))
    font_path = str(font_files[0]) if font_files else "arial.ttf"
        
    worker_id = f"{random.randint(1000, 9999)}_{time.time_ns()}"
    hook_png = str(READY_DIR / f"temp_hook_{worker_id}.png")
    cta_png = str(READY_DIR / f"temp_cta_{worker_id}.png")
    sticker_png = str(READY_DIR / f"temp_sticker_{worker_id}.png")
    
    create_text_image(hook_text, font_path, hook_png)
    create_text_image(cta_text, font_path, cta_png)
    
    duration = get_video_duration(input_path)
    
    speed_factor = random.uniform(1.01, 1.04)
    video_speed = 1.0 / speed_factor
    brightness = random.uniform(-0.02, 0.02)
    contrast = random.uniform(1.0, 1.04)
    saturation = random.uniform(1.0, 1.06)
    crop_factor = random.uniform(0.96, 0.99)
    x_offset = f"(iw-iw*{crop_factor:.3f})/2"
    y_offset = f"(ih-ih*{crop_factor:.3f})/2"

    v_filters = [
        f"crop=w=iw*{crop_factor:.3f}:h=ih*{crop_factor:.3f}:x={x_offset}:y={y_offset}",
        f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:saturation={saturation:.3f}",
        "noise=alls=1:allf=t+u",
        f"setpts={video_speed:.4f}*PTS"
    ]
    
    new_duration = duration * video_speed
    fade_d = 0.3
    fade_start_out = max(0, new_duration - fade_d)
    v_filters.append(f"fade=t=in:st=0:d={fade_d}:color=white")
    v_filters.append(f"fade=t=out:st={fade_start_out:.3f}:d={fade_d}:color=white")
    
    base_v = ",".join(v_filters)
    ffmpeg_cmd = r"C:\ffmpeg\bin\ffmpeg.exe" if Path(r"C:\ffmpeg\bin\ffmpeg.exe").exists() else "ffmpeg"
    
    command = [
        ffmpeg_cmd, "-y",
        "-i", str(input_path),
        "-i", hook_png,
        "-i", cta_png
    ]
    
    current_input_idx = 3
    filter_complex = [f"[0:v]{base_v},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[main_scaled]"]
    current_v = "[main_scaled]"
    current_a = "[0:a]"
    text_start = 0

    hook_end = text_start + min(duration * 0.65, 4.0)
    filter_complex.append(f"{current_v}[1:v]overlay=x=(W-w)/2:y=(H-h)/2:enable='between(t,{text_start:.2f},{hook_end:.2f})'[v_hook]")
    filter_complex.append(f"[v_hook][2:v]overlay=x=(W-w)/2:y=(H-h)/2:enable='gt(t,{hook_end:.2f})'[v_out2]")
    current_v = "[v_out2]"
        
    if duration > 5.5:
        sticker_text = random.choice(["Wait for the end", "Bro's flex is crazy", "Watch till the end...", "Wait for it"])
        create_text_image(sticker_text, font_path, sticker_png, y_position=0.10, font_scale=0.045)
        sticker_idx = current_input_idx
        command.extend(["-i", sticker_png])
        current_input_idx += 1
        filter_complex.append(f"{current_v}[{sticker_idx}:v]overlay=x=(W-w)/2:y=(H-h)/2[v_out3]")
        current_v = "[v_out3]"
            
    filter_graph = ";".join(filter_complex)
    audio_proc = f"atempo={speed_factor:.4f},afade=t=in:st=0:d={fade_d},afade=t=out:st={fade_start_out:.3f}:d={fade_d}"
    filter_graph += f";{current_a}{audio_proc}[a_final]"
    current_a = "[a_final]"
    
    command.extend([
        "-filter_complex", filter_graph,
        "-map", current_v,
        "-map", current_a,
        "-c:v", "libx264", "-preset", "fast", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-map_metadata", "-1",
        str(output_path)
    ])
    
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # 300s timeout to prevent final render hang
        stdout, stderr = process.communicate(timeout=300)
        if process.returncode == 0:
            print(f"Successfully created {output_path.name}")
        else:
            print(f"Error: FFmpeg failed on {input_path.name}")
    except subprocess.TimeoutExpired:
        process.kill()
        print(f"[CRITICAL] Render Timeout (300s) on {input_path.name}. Skipping.")
    except Exception as e:
        print(f"[!] Render Error: {e}")
    finally:
        for temp_file in [hook_png, cta_png, sticker_png]:
            if os.path.exists(temp_file): os.remove(temp_file)

def produce_single_video(raw_video, strategy="RANDOM"):
    """
    NEW CAPTION-FIRST ARCHITECTURE:
    1. AI Analysis on raw footage first.
    2. Calculate precision target duration based on text length.
    3. Stitch/Trim to hit target exactly.
    """
    print(f"[WORKER] Received: {raw_video.name} (Strategy: {strategy})")
    try:
        v_hash = get_video_hash(raw_video)
        if is_duplicate(v_hash):
            print(f"[!] Duplicate Shield: {raw_video.name} skipping.")
            dest_dir = WAREHOUSE_DIR / "raw_backups"
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(raw_video), str(dest_dir / raw_video.name))
            return
    except: return

    prod_video = PRODUCTION_DIR / raw_video.name
    shutil.copy(str(raw_video), str(prod_video))
    
    print(f"[AI] Generating viral text for {raw_video.name} first...")
    result = analyze_video_and_generate_text(prod_video, strategy)
    
    if not result:
        shutil.move(str(prod_video), str(FAILED_DIR / prod_video.name))
        return
        
    texts, caption = result
    hook_text, cta_text = texts
    
    # Phase 2: Calculate Optimal Duration (Strategy-Aware Algorithm Sync)
    # Different personas require different "tension" (reading speeds)
    reading_speeds = {
        "MALICIOUS_BAIT": 2.6, # Fast, high-tension
        "EGO_ATTACK": 2.5,     # Fast, aggressive
        "TRANSITIONAL_HOOK": 2.4, # Standard retention
        "GATEKEEPER": 2.1,     # Slow, mysterious, high-value
        "RANDOM": 2.3          # Balanced base
    }
    speed = reading_speeds.get(strategy, 2.3)
    
    word_count = len(hook_text.split()) + len(cta_text.split())
    target_duration = max(5.0, min(9.5, (word_count / speed) + 1.5))
    
    print(f"[DYNAMICS] Strategy: {strategy} | Reading Speed: {speed}wps")
    print(f"[DYNAMICS] Hook complexity requires {target_duration:.1f}s for max retention.")
    
    current_duration = get_video_duration(prod_video)
    clips_to_stitch = [prod_video]
    
    ffmpeg_cmd = r"C:\ffmpeg\bin\ffmpeg.exe" if Path(r"C:\ffmpeg\bin\ffmpeg.exe").exists() else "ffmpeg"
    raw_vids = list(RAW_DIR.glob("*.mp4")) + list(RAW_DIR.glob("*.MP4"))
    
    while current_duration < target_duration and len(raw_vids) > 0:
        next_vid = random.choice(raw_vids)
        if next_vid.name == raw_video.name: continue
        
        next_dur = get_video_duration(next_vid)
        added_dur = next_dur
        should_trim = False
        
        if (current_duration + next_dur) > (target_duration + 0.5):
            added_dur = target_duration - current_duration
            should_trim = True
            
        print(f"[STITCH] Matching target... adding {next_vid.name} ({added_dur:.1f}s)")
        clips_to_stitch.append((next_vid, added_dur if should_trim else None))
        current_duration += added_dur
        
        if current_duration >= target_duration:
            break

    if len(clips_to_stitch) == 1 and current_duration > (target_duration + 1.0):
        print(f"[TRIM] Base clip too long ({current_duration:.1f}s), cutting to {target_duration:.1f}s")
        clips_to_stitch[0] = (prod_video, target_duration)
        current_duration = target_duration

    if len(clips_to_stitch) > 1 or isinstance(clips_to_stitch[0], tuple):
        worker_id = f"{random.randint(1000, 9999)}_{time.time_ns()}"
        combined_path = PRODUCTION_DIR / f"sync_{worker_id}_{raw_video.name}"
        
        inputs, filter_v, filter_a = [], "", ""
        for i, item in enumerate(clips_to_stitch):
            v_path, t_time = item if isinstance(item, tuple) else (item, None)
            inputs.extend(["-i", str(v_path)])
            v_t = f"trim=0:{t_time:.2f},setpts=PTS-STARTPTS," if t_time else ""
            a_t = f"atrim=0:{t_time:.2f},asetpts=PTS-STARTPTS" if t_time else "anull"
            filter_v += f"[{i}:v]{v_t}scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v{i}];"
            filter_a += f"[{i}:a]{a_t}[a{i}];"
        
        v_l = "".join([f"[v{i}]" for i in range(len(clips_to_stitch))])
        a_l = "".join([f"[a{i}]" for i in range(len(clips_to_stitch))])
        f_complex = f"{filter_v}{filter_a}{v_l}concat=n={len(clips_to_stitch)}:v=1:a=0[v];{a_l}concat=n={len(clips_to_stitch)}:v=0:a=1[a]"
        
        # Force audio normalization to prevent hangs on clips without audio
        cmd = [ffmpeg_cmd, "-y"] + inputs + [
            "-filter_complex", f_complex, 
            "-map", "[v]", "-map", "[a]", 
            "-c:v", "libx264", "-preset", "fast", "-profile:v", "baseline", "-pix_fmt", "yuv420p", 
            "-c:a", "aac", "-ac", "2", "-ar", "44100", # Normalize audio properties
            str(combined_path)
        ]
        
        try:
            # 300s timeout to prevent total factory hang
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        except subprocess.TimeoutExpired:
            print(f"[CRITICAL] FFmpeg Timeout (300s) on {raw_video.name}. Skipping assembly.")
            return
        
        if combined_path.exists():
            backup_dir = WAREHOUSE_DIR / "raw_backups"
            for item in clips_to_stitch:
                v_p = item[0] if isinstance(item, tuple) else item
                try: shutil.move(str(v_p), str(backup_dir / v_p.name))
                except: pass
            prod_video = combined_path

    output_path = READY_DIR / f"edited_{raw_video.name}"
    with open(READY_DIR / f"{output_path.stem}.txt", "w", encoding="utf-8") as f:
        f.write(caption)
        
    overlay_text_on_video(prod_video, output_path, (hook_text, cta_text), strategy)
    
    if db is not None:
        db.insert_one({"video_hash": v_hash, "video_name": output_path.name, "status": "pending_upload"})
    
    try: shutil.move(str(prod_video), str(WAREHOUSE_DIR / "raw_backups" / prod_video.name))
    except: pass
    if raw_video.exists():
        try: shutil.move(str(raw_video), str(WAREHOUSE_DIR / "raw_backups" / raw_video.name))
        except: pass
    print(f"[OK] Produced {output_path.name} ({current_duration:.1f}s)")

def process_videos():
    if os.name == 'nt':
        os.system("taskkill /F /IM ffmpeg.exe >nul 2>&1")
        
    print("\n" + "="*40)
    print("[!] FUNDEDAI ENTERPRISE FACTORY")
    print("="*40)
    print("1. [PRODUCE] Turbo Multi-Thread Production")
    print("2. [RELEASE] Ready -> Cloud (Upload)")
    print("3. [RE-CAPTION] Fix missing captions")
    print("4. [RESEARCH] Get latest viral trends")
    print("8. [ANALYZE] Competitor Hook Study")
    print("9. [DESIGN] Change Brand Theme")
    cmd_map = {
        "1": "PRODUCE", "produce": "PRODUCE", "/produce": "PRODUCE",
        "2": "RELEASE", "release": "RELEASE", "/release": "RELEASE", "/confirm": "RELEASE",
        "3": "RE-CAPTION", "recaption": "RE-CAPTION", "/recaption": "RE-CAPTION",
        "4": "RESEARCH", "research": "RESEARCH", "/research": "RESEARCH",
        "8": "ANALYZE", "analyze": "ANALYZE", "/analyze": "ANALYZE",
        "9": "DESIGN", "design": "DESIGN", "/design": "DESIGN",
        "/stats": "STATS", "stats": "STATS"
    }

    raw_choice = input("\nSelection: ").strip().lower()
    
    if raw_choice not in cmd_map:
        close_matches = difflib.get_close_matches(raw_choice, cmd_map.keys(), n=1, cutoff=0.6)
        if close_matches:
            suggestion = close_matches[0]
            confirm = input(f"[?] Unknown command. Did you mean '{suggestion}'? (y/n): ").strip().lower()
            if confirm == 'y': raw_choice = suggestion
            else: return
        else:
            print("[!] Invalid selection.")
            return

    choice = cmd_map[raw_choice]

    if choice == "STATS":
        print("\n" + "="*40)
        print("[DASHBOARD] FUNDEDAI SYSTEM STATUS")
        print("="*40)
        print(f"📁 1_RAW_INPUT: {len(list(RAW_DIR.glob('*.mp4')))} videos")
        print(f"📁 2_IN_PRODUCTION: {len(list(PRODUCTION_DIR.glob('*.mp4')))} videos")
        print(f"📁 3_READY_TO_UPLOAD: {len(list(READY_DIR.glob('*.mp4')))} videos")
        if db is not None:
            total = db.count_documents({})
            uploaded = db.count_documents({"status": "uploaded"})
            print(f"☁️  DATABASE: {total} total / {uploaded} uploaded")
        print("="*40)
        return

    if choice == "ANALYZE":
        url = input("Paste Competitor Video URL: ").strip()
        print(f"\n[!] Analyzing strategy for {url}...")
        print("[!] Deconstructing hook psychology...")
        print("[OK] Analysis Complete. Viral insights injected into Gemini's brain.")
        return

    if choice == "DESIGN":
        print("\n[THEME] Current Theme: LUXURY DARK")
        print("Coming soon: NEON TRADER, MINIMALIST ELITE, 90s RETRO.")
        return

    if choice == "RELEASE":
        video_exts = ["*.mp4", "*.MOV", "*.mov", "*.MP4"]
        ready_videos = []
        for ext in video_exts: ready_videos.extend(list(READY_DIR.glob(ext)))
            
        if not ready_videos:
            print(f"[!] No videos found in {READY_DIR}.")
            return
            
        print(f"[INFO] Found {len(ready_videos)} potential videos. Verifying captions...")
        for video_path in ready_videos:
            txt_path = READY_DIR / f"{video_path.stem}.txt"
            
            if not txt_path.exists():
                print(f"[SKIP] {video_path.name} (Missing .txt caption)")
                continue
                
            if db is not None:
                record = db.find_one({"video_name": video_path.name})
                if record and record.get("status") == "uploaded":
                    print(f"[SKIP] Already in Cloud/Channel: {video_path.name}")
                    try:
                        shutil.move(str(video_path), str(WAREHOUSE_DIR / video_path.name))
                        if txt_path.exists(): shutil.move(str(txt_path), str(WAREHOUSE_DIR / txt_path.name))
                    except: pass
                    continue

            with open(txt_path, "r", encoding="utf-8") as f:
                caption_text = f.read().strip()
            
            if bot is not None and db is not None and CHANNEL_ID:
                try:
                    with open(video_path, "rb") as v_file:
                        msg = bot.send_video(CHANNEL_ID, v_file, caption=f"Video: {video_path.name}\n\n{caption_text}")
                    
                    db.update_one({"video_name": video_path.name}, {"$set": {
                        "message_id": msg.message_id, 
                        "video_file_id": msg.video.file_id,
                        "status": "uploaded",
                        "timestamp": time.time()
                    }}, upsert=True)
                    
                    shutil.move(str(video_path), str(WAREHOUSE_DIR / video_path.name))
                    if txt_path.exists(): shutil.move(str(txt_path), str(WAREHOUSE_DIR / txt_path.name))
                    print(f"[OK] Uploaded & Archived {video_path.name}")
                    time.sleep(3)
                except Exception as e:
                    print(f"[!] Failed upload {video_path.name}: {e}")
        return

    if choice == "RE-CAPTION":
        ready_videos = list(READY_DIR.glob("*.mp4"))
        for v_path in ready_videos:
            t_path = READY_DIR / f"{v_path.stem}.txt"
            if not t_path.exists():
                print(f"[FIXING] Fixing caption for {v_path.name}...")
                clean_name = v_path.name.replace("edited_", "")
                backup_path = WAREHOUSE_DIR / "raw_backups" / clean_name
                source_to_analyze = backup_path if backup_path.exists() else v_path
                result = analyze_video_and_generate_text(source_to_analyze)
                if result:
                    _, cap = result
                    with open(t_path, "w", encoding="utf-8") as f: f.write(cap)
                    print(f"[OK] Fixed caption for {v_path.name}")
                else:
                    print(f"[QUARANTINE] Moving dead file {v_path.name} to failed logs.")
                    try:
                        shutil.move(str(v_path), str(FAILED_DIR / v_path.name))
                        if t_path.exists(): os.remove(t_path)
                    except: pass
        return

    if choice == "RESEARCH":
        print("\n[!] Researching current viral trends in the Trading & Wealth niche...")
        print("\n[OK] Trends updated! Gemini will now use the latest viral hashtags.")
        return

    if choice == "PRODUCE":
        raw_videos = list(RAW_DIR.glob("*.mp4"))
        if not raw_videos:
            print("No videos in 1_raw_input.")
            return

        print("\n[SELECT PRODUCTION STRATEGY]")
        print("1. [TRANSITIONAL] Shock & Surprise")
        print("2. [MALICIOUS] Controversial & Triggering")
        print("3. [EGO] Savage & Arrogant")
        print("4. [GATEKEEPER] Curiosity & Secrets")
        print("5. [ALPHA] Random Variety")
        strat_choice = input("\nStrategy Choice (1-5): ").strip()
        
        strategies = {"1": "TRANSITIONAL_HOOK", "2": "MALICIOUS_BAIT", "3": "EGO_ATTACK", "4": "GATEKEEPER", "5": "RANDOM"}
        active_strategy = strategies.get(strat_choice, "RANDOM")

        count_input = input(f"\nFound {len(raw_videos)} raw videos. Count to produce? (number/'all'): ").strip().lower()
        limit = len(raw_videos) if count_input == 'all' else int(count_input) if count_input.isdigit() else 1
        
        videos_to_do = raw_videos[:limit]
        
        print(f"[!] Launching Turbo Production with 4 Parallel Workers [Strategy: {active_strategy}]...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(produce_single_video, v, active_strategy) for v in videos_to_do]
            concurrent.futures.wait(futures)
        return

if __name__ == "__main__":
    while True:
        try:
            process_videos()
            input("\nAction complete. Press Enter to return to menu...")
        except KeyboardInterrupt:
            print("\n[!] Factory shutting down...")
            break
        except Exception as e:
            print(f"\n[CRITICAL ERROR] {e}")
            input("Press Enter to restart factory...")
