"""Map each track's energy so build.py can land a track's "hit" on the Short's payoff word.
   python music/analyse.py   -> music/library.json"""
import json
import subprocess
from pathlib import Path

import numpy as np

FF = r"C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.exe"
HERE = Path(__file__).parent
MOOD = {"Volatile Reaction": "driving tension", "Crossing the Chasm": "epic build", "Impact Prelude": "big cinematic",
        "Heroic Age": "triumphant", "Hitman": "suspense"}
lib = {}
for f in sorted(HERE.glob("*.mp3")):
    pcm = subprocess.run([FF, "-v", "error", "-i", str(f), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(pcm, np.float32)
    win = 4000  # 0.5 s
    rms = np.sqrt(np.convolve(a ** 2, np.ones(win) / win, "valid")[::win] + 1e-12)
    db = 20 * np.log10(rms)
    # a "hit" = loudness jumps >= 6 dB within 1 s after at least 2 s of quieter music
    hits = [round(i * 0.5, 1) for i in range(4, len(db) - 2)
            if db[i + 2] - db[i - 4:i].mean() >= 6 and db[i + 2] > db.max() - 12]
    dedup = [h for k, h in enumerate(hits) if not k or h - hits[k - 1] > 4]
    lib[f.stem] = {"mood": MOOD.get(f.stem, ""), "duration": round(len(a) / 8000, 1), "hits": dedup[:12],
                   "loudness_db_per_2s": [round(float(x), 1) for x in db[::4]]}
    print(f"{f.stem:22} {lib[f.stem]['mood']:16} hits at {dedup[:8]}")
(HERE / "library.json").write_text(json.dumps(lib, indent=1))
