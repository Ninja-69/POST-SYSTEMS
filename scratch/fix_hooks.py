import os
import subprocess
from pathlib import Path

HOOKS_DIR = Path("hooks")
TEMP_DIR = Path("hooks_v2")
TEMP_DIR.mkdir(exist_ok=True)

FFMPEG = r"C:\ffmpeg\bin\ffmpeg.exe" if os.path.exists(r"C:\ffmpeg\bin\ffmpeg.exe") else "ffmpeg"

def convert_to_h264():
    files = list(HOOKS_DIR.glob("*.mp4"))
    print(f"Converting {len(files)} hooks to H.264 for RDP visibility...")
    
    for f in files:
        output = TEMP_DIR / f.name
        print(f"Processing {f.name}...")
        cmd = [
            FFMPEG, "-y", "-i", str(f),
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            str(output)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print("\nConversion complete. Moving files back to 'hooks'...")
    for f in files:
        f.unlink() # Delete old HEVC file
        (TEMP_DIR / f.name).rename(HOOKS_DIR / f.name)
    
    os.rmdir(TEMP_DIR)
    print("All hooks are now RDP-Compatible H.264!")

if __name__ == "__main__":
    convert_to_h264()
