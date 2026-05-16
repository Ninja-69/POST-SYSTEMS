import sys

def find_non_ascii(filepath):
    with open(filepath, 'rb') as f:
        content = f.read().decode('utf-8', errors='ignore')
    
    for i, line in enumerate(content.splitlines()):
        if any(ord(c) > 127 for c in line):
            non_ascii = [c for c in line if ord(c) > 127]
            print(f"Line {i+1} has {len(non_ascii)} non-ASCII characters.")

if __name__ == "__main__":
    find_non_ascii(r'C:\Users\Administrator\.gemini\antigravity\scratch\luxury_video_editor\main.py')
