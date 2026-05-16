import os
import re
import requests
from pathlib import Path

# Paths
HOOKS_DIR = Path("hooks")
HOOKS_DIR.mkdir(exist_ok=True)
MARKDOWN_FILE = Path(r"C:\Users\Administrator\.gemini\antigravity\brain\b9958fda-4605-4d68-8bec-da1d006c39b3\.system_generated\steps\1296\content.md")

def download_hooks():
    if not MARKDOWN_FILE.exists():
        print("❌ Scraped content file not found.")
        return

    with open(MARKDOWN_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Find all .mp4 download links
    links = re.findall(r'https?://[^\s)]+\.mp4', content)
    unique_links = list(set(links))[:20] # Get top 20 unique links

    print(f"Found {len(unique_links)} potential hook transitions. Starting download...")

    success_count = 0
    for link in unique_links:
        filename = link.split("/")[-1]
        dest = HOOKS_DIR / filename
        
        if dest.exists():
            print(f"Skipping {filename} (Already exists)")
            success_count += 1
            continue

        try:
            print(f"Downloading {filename}...")
            response = requests.get(link, timeout=15)
            if response.status_code == 200:
                with open(dest, "wb") as f:
                    f.write(response.content)
                print(f"Saved: {filename}")
                success_count += 1
            else:
                print(f"Failed {filename} (HTTP {response.status_code})")
        except Exception as e:
            print(f"Error downloading {filename}: {e}")

    print(f"\nHook Vault populated: {success_count}/20 files secured.")

if __name__ == "__main__":
    download_hooks()
