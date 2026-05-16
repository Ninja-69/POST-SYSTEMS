import subprocess

def run_ffmpeg_test():
    # Construct a sample FFmpeg command based on the failing logic
    # We will use the exact command from main.py, substituting dummy files
    # to see what part of the filter_complex fails.
    
    # We need dummy files to exist for ffmpeg to parse the filter graph
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=10", "-c:v", "libx264", "dummy_main.mp4"])
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=1080x1920:d=2", "-c:v", "libx264", "dummy_hook.mp4"])
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=white:s=100x100:d=1", "-vframes", "1", "dummy_png.png"])
    
    command = [
        "ffmpeg", "-y",
        "-i", "dummy_main.mp4",
        "-i", "dummy_png.png", # hook
        "-i", "dummy_png.png", # cta
        "-i", "dummy_png.png", # watermark
        "-i", "dummy_hook.mp4" # hook video
    ]
    
    filter_complex = []
    
    base_v = "crop=w=iw*1.0:h=ih*1.0:x=0:y=0,eq=brightness=0:contrast=1:saturation=1,noise=alls=1:allf=t+u,setpts=1.0*PTS,fade=t=in:st=0:d=0.3:color=white,fade=t=out:st=9.7:d=0.3:color=white"
    
    filter_complex.append(f"[0:v]{base_v}[main_v]")
    filter_complex.append(f"[4:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[hook_v]")
    filter_complex.append(f"[hook_v][main_v]concat=n=2:v=1:a=0[base_v]")
    filter_complex.append(f"[4:a][0:a]concat=n=2:v=0:a=1[base_a]")
    
    current_v = "[base_v]"
    current_a = "[base_a]"
    
    filter_complex.append(f"{current_v}[3:v]overlay=x='if(lt(mod(t,4),2),10,W-w-10)':y='if(lt(mod(t,6),3),10,H-h-10)':alpha=0.4[v_water]")
    current_v = "[v_water]"
    
    filter_complex.append(f"{current_v}[1:v]overlay=x=(W-w)/2:y=(H-h)/2:enable='between(t,0,4.0)'[v_hook]")
    filter_complex.append(f"[v_hook][2:v]overlay=x=(W-w)/2:y=(H-h)/2:enable='gt(t,4.0)'[v_out2]")
    current_v = "[v_out2]"
    
    filter_graph = ";".join(filter_complex)
    
    command.extend([
        "-filter_complex", filter_graph,
        "-map", current_v,
        "-map", current_a,
        "test_output.mp4"
    ])
    
    print("Running Command:", " ".join(command))
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    print("RETURN CODE:", result.returncode)
    print("STDERR:\n", result.stderr)

if __name__ == "__main__":
    run_ffmpeg_test()
