"""One spec in, one finished Short out (real footage + telestrator + voiceover).

  uv run --no-project --with edge-tts==7.3.1 --with kokoro-onnx==0.4.7 --with faster-whisper==1.2.1 --with soundfile python build.py specs/<id>.json [--skip-vo] [--still=F] [--frames=A-B]

1. Microsoft neural voice via edge-tts (house voice en-US-AndrewNeural), which also returns exact word times.
   A Kokoro voice name (e.g. bm_george) still works offline; then
2. faster-whisper hears it back; words are matched to the script, so every spoken word has a frame.
3. The `timeline` (play / slow-mo / freeze segments of the source clip) and the `beats` (graphics, zooms)
   say WHEN by spoken word: "glove", "glove#2", "glove+0.3", "glove#2-0.2", a number of seconds, or "end".
4. Remotion renders picture + audio (voice + synthesised pad; commentary muted by default) from out/<id>/props.json;
   ffmpeg normalises to -16 LUFS (measured on the reference channel).
Output: out/<id>/<id>.mp4 and out/<id>/sheet.jpg (1 frame per second, for QA).
Run ONE build at a time: Kokoro, Whisper and Remotion each need most of this laptop's free RAM.
"""
import difflib
import gc
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

HERE = Path(__file__).parent
FPS = 30
SR = 24000
GRAPHICS = {"ring", "spot", "marker", "arrow", "tag", "label", "text"}
LEAD = 0.04  # seconds before the first word (Kokoro's own leading silence is trimmed): the hook starts on frame 1
FFBIN = Path(r"C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin")
WIN = sys.platform == "win32"  # else the GitHub runner (render.yml): tools on PATH
FFMPEG, FFPROBE = (str(FFBIN / "ffmpeg.exe"), str(FFBIN / "ffprobe.exe")) if WIN else ("ffmpeg", "ffprobe")
NPX = r"C:\Program Files\nodejs\npx.cmd" if WIN else "npx"

norm = lambda s: re.sub(r"[^a-z0-9']", "", s.lower())
tokens = lambda text: [t for t in re.findall(r"\S+", text) if re.search(r"[A-Za-z0-9]", t)]


def align(toks, words):
    """toks: script tokens. words: [(text, start, end)] from Whisper. Returns [(start, end)] per token.
    From psych-channel-renders/generate_vo_kokoro.py: unmatched words are interpolated between neighbours."""
    t = [None] * len(toks)
    sm = difflib.SequenceMatcher(None, [norm(x) for x in toks], [norm(w[0]) for w in words], autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            t[blk.a + k] = (words[blk.b + k][1], words[blk.b + k][2])
    if not any(t):
        raise SystemExit("whisper matched no words - check the audio")
    i = 0
    while i < len(t):
        if t[i]:
            i += 1
            continue
        j = i
        while j < len(t) and not t[j]:
            j += 1
        lo = t[i - 1][1] if i > 0 else max(0.0, t[j][0] - 0.3 * (j - i))
        hi = t[j][0] if j < len(t) else lo + 0.3 * (j - i)
        weights = [max(1, len(norm(x))) for x in toks[i:j]]
        acc, total = lo, sum(weights)
        for k, wgt in zip(range(i, j), weights):
            step = (hi - lo) * wgt / total
            t[k] = (acc, acc + step)
            acc += step
        i = j
    return t


def edge(text, v, speed):
    """One take from Microsoft's neural voices (edge-tts, no key, needs internet). Returns samples and
    [(word, start, end)] straight from the service, trimmed to the speech: no Whisper, no drift."""
    import asyncio
    import edge_tts

    async def go():
        c = edge_tts.Communicate(text, v, rate=f"{round((speed - 1) * 100):+d}%", boundary="WordBoundary")
        mp3, words = b"", []
        async for ch in c.stream():
            if ch["type"] == "audio":
                mp3 += ch["data"]
            elif ch["type"] == "WordBoundary":
                words.append((ch["text"], ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7))
        return mp3, words

    mp3, words = asyncio.run(go())
    a = np.frombuffer(subprocess.run([FFMPEG, "-loglevel", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", str(SR), "pipe:1"],
                                     input=mp3, capture_output=True, check=True).stdout, np.float32)
    t0, t1 = max(0.0, words[0][1] - 0.02), words[-1][2] + 0.08
    return a[int(t0 * SR): int(t1 * SR)], [(w, s - t0, e - t0) for w, s, e in words]


def voice(spec, wav):
    """Writes the voice to wav. Returns word times for edge voices, None for Kokoro (Whisper finds them)."""
    v = spec.get("voice", "en-US-AndrewNeural")
    lines = spec.get("vo_lines") or [{"text": spec["vo"], "pause": 0}]
    # Emotion: each line gets its own pace (slow = tension, fast = action) and its own silence after it
    # (a held breath before the reveal). The joined text must equal `vo`.
    if " ".join(x["text"] for x in lines).split() != spec["vo"].split():
        raise SystemExit("vo_lines joined must equal vo word for word")
    if "Neural" in v:
        parts, words, at = [np.zeros(int(LEAD * SR), np.float32)], [], LEAD
        for x in lines:
            a, w = edge(x["text"], v, x.get("speed", spec.get("speed", 1.05)))
            words += [(t, s + at, e + at) for t, s, e in w]
            gap = np.zeros(int(x.get("pause", 0.12) * SR), np.float32)
            parts += [a, gap]
            at += (len(a) + len(gap)) / SR
        chain(np.concatenate(parts[:-1]), wav)
        return words
    kokoro(spec, wav, v, lines)


def chain(audio, wav):
    raw = wav.with_name("vo_raw.wav")
    sf.write(raw, audio, SR)
    # voice chain: rumble cut, the 2.6 kHz notch (AI voices are already hot at 2-4 kHz, see psych-channel memory),
    # gentle compression, de-ess. No time-stretching: it made words sound "stuck" on the psych channel.
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(raw), "-af",
                    "highpass=f=70,equalizer=f=2600:t=q:w=1.2:g=-2.5,equalizer=f=180:t=q:w=1:g=1.5,"
                    "acompressor=threshold=-20dB:ratio=3:attack=5:release=90:makeup=2,deesser=i=0.3",
                    "-ar", str(SR), str(wav)], check=True)


def kokoro(spec, wav, v, lines):
    from kokoro_onnx import Kokoro
    k = Kokoro(str(HERE / "models" / "kokoro-v1.0.int8.onnx"), str(HERE / "models" / "voices-v1.0.bin"))
    lang = "en-gb" if v.startswith("b") else "en-us"

    def trim(a):  # Kokoro pads silence around every take
        on = np.flatnonzero(np.abs(a) > 0.01)
        return a[max(0, on[0] - int(0.02 * SR)): on[-1] + int(0.06 * SR)] if len(on) else a

    parts = []
    for x in lines:
        a, sr = k.create(x["text"], voice=v, speed=x.get("speed", spec.get("speed", 1.15)), lang=lang)
        assert sr == SR, sr
        parts += [trim(a), np.zeros(int(x.get("pause", 0.12) * SR))]
    chain(np.concatenate([np.zeros(int(LEAD * SR))] + parts[:-1]), wav)
    del k
    gc.collect()  # Kokoro and Whisper together don't fit in this laptop's free RAM


def timings(spec, wav):
    from faster_whisper import WhisperModel
    wm = WhisperModel("small", device="cpu", compute_type="int8", local_files_only=True)
    a, sr = sf.read(wav)  # pass samples, not a path: faster-whisper 1.2.1's file decoder breaks on current PyAV
    a = np.interp(np.arange(0, len(a), sr / 16000), np.arange(len(a)), a).astype(np.float32)
    segs, _ = wm.transcribe(a, language="en", word_timestamps=True, initial_prompt=spec["vo"], vad_filter=False)
    words = [(w.word, w.start, w.end) for s in segs for w in (s.words or [])]
    toks = tokens(spec["vo"])
    return toks, align(toks, words)


def pad(pub, seconds, vo):
    """A soft synthesised chord pad ~10 dB under the voice (the house sound: no SFX, commentary muted).
    Synthesised, so nothing to license and nothing for Content ID to match."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    chords = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]  # Am F C G, 4 s each
    out = np.zeros(n)
    for i, ch in enumerate(chords * (int(seconds // 16) + 1)):
        a, b = int(i * 4 * SR), min(n, int((i + 1) * 4 * SR + 0.5 * SR))
        if a >= n:
            break
        tt = t[a:b] - t[a]
        # no fade-in on the first chord: a pad rising from silence leaves a hole at the loop point
        env = (np.minimum(1, tt / 0.6) if i else 1) * np.minimum(1, (tt[-1] - tt + 1e-3) / 0.5)
        for m in ch:
            f = 440 * 2 ** ((m - 69) / 12)
            out[a:b] += env * (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(2 * np.pi * 2 * f * tt + 0.5) + 0.15 * np.sin(2 * np.pi * f / 2 * tt))
    out *= 1 + 0.15 * np.sin(2 * np.pi * 2 * t)  # slow pulse so it doesn't sit dead still
    v, _ = sf.read(vo)
    speech = v[np.abs(v) > 0.02]
    target = np.sqrt(np.mean(speech ** 2)) * 10 ** (-10 / 20) if len(speech) else 0.02
    out *= target / (np.sqrt(np.mean(out ** 2)) + 1e-9)
    sf.write(pub / "pad.wav", out, SR)


def score(pub, spec, seconds, vo, cue_time):
    """Background music with an emotional shape. spec["music"] = {"track": "Impact Prelude", "hit": 16.5,
    "on": "Short#2", "db": -14, "moves": [{"at": "Look#2", "db": -26, "fade": 0.4}, {"at": "Short#2", "db": -10, "fade": 0.1}]}
    - the track's `hit` (seconds into the track, see music/library.json) lands on the spoken word `on`;
    - `db` = level under the voice's speech RMS; `moves` change it from a cue on (dip before a reveal, swell on it).
    Tracks are Kevin MacLeod / incompetech, CC-BY 4.0: the publisher credits them in the description."""
    m = spec["music"]
    # carve the voice's presence band out of the music (2-5 kHz is what masks speech on phone speakers, knowledge/voice.md)
    pcm = subprocess.run([FFMPEG, "-v", "error", "-i", str(HERE / "music" / f"{m['track']}.mp3"), "-ac", "1", "-ar", str(SR),
                          "-af", "equalizer=f=3000:t=q:w=0.9:g=-4", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    track = np.frombuffer(pcm, np.float32).astype(np.float64)
    n = int(seconds * SR)
    start = int((m.get("hit", 0) - (cue_time(m["on"]) if "on" in m else 0)) * SR)
    seg = np.zeros(n)
    src = track[max(0, start): max(0, start) + n]
    seg[max(0, -start): max(0, -start) + len(src)] = src[: n - max(0, -start)]
    v, _ = sf.read(vo)
    speech = v[np.abs(v) > 0.02]
    ref = np.sqrt(np.mean(speech ** 2)) if len(speech) else 0.05
    level = ref * 10 ** (m.get("db", -14) / 20) / (np.sqrt(np.mean(seg[seg != 0] ** 2)) + 1e-9)
    gain_db = np.full(n, 0.0)
    for mv in sorted(m.get("moves", []), key=lambda x: cue_time(x["at"])):
        i = int(cue_time(mv["at"]) * SR)
        f = max(1, int(mv.get("fade", 0.3) * SR))
        target = mv["db"] - m.get("db", -14)
        prev = gain_db[min(i, n - 1)]
        ramp = np.linspace(prev, target, f)
        gain_db[i: i + f] = ramp[: max(0, n - i)]
        gain_db[i + f:] = target
    out = seg * level * 10 ** (gain_db / 20)
    # fade-out ends exactly at the video's end (seconds - 0.5), then silence: no chop, no click on loop; no fade-in
    e = int((seconds - 0.5) * SR)
    fo = int(m.get("fade_out", 0.3) * SR)
    out[e - fo: e] *= np.linspace(1, 0, fo)
    out[e:] = 0
    sf.write(pub / "music.wav", out, SR)


def sfx(music_wav, spec, vo, cue_time):
    """Diegetic sound on the moments the picture shows, nothing automatic: spec["sfx"] = [{"at": 19.1, "s": "wooden-baseball-bat-hit-02",
    "db": -8, "len": 1.5}]. `s` is an SFXMint slug (sfxmint.com, CC0-1.0, no key, no credit needed; find one with
    https://sfxmint.com/api/v1/search?q=crowd+gasp), `db` is vs the voice's speech RMS, `len` trims with a short fade.
    The editor places each one by stepping the cut frame by frame (bail lifts, bat meets ball), never on a cut or a graphic."""
    lib = HERE / "music" / "sfx"
    lib.mkdir(parents=True, exist_ok=True)
    bed, sr = sf.read(music_wav)
    v, _ = sf.read(vo)
    speech = v[np.abs(v) > 0.02]
    ref = np.sqrt(np.mean(speech ** 2)) if len(speech) else 0.05
    for x in spec["sfx"]:
        f = lib / f"{x['s']}.wav"
        if not f.exists():
            subprocess.run(["curl", "-sf", "-m", "60", "-L", "-o", str(f), f"https://sfxmint.com/dl/{x['s']}.wav"], check=True)
        pcm = subprocess.run([FFMPEG, "-v", "error", "-i", str(f), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                             capture_output=True, check=True).stdout
        s = np.frombuffer(pcm, np.float32).astype(np.float64)
        s = s * ref * 10 ** (x.get("db", -12) / 20) / (np.sqrt(np.mean(s[np.abs(s) > 0.01] ** 2)) + 1e-9)  # level by its audible part
        if "len" in x:
            s = s[: int(x["len"] * sr)].copy()
            fo = min(len(s), int(0.3 * sr))
            s[len(s) - fo:] *= np.linspace(1, 0, fo)
        i = int(cue_time(x["at"]) * sr)
        n = max(0, min(len(s), len(bed) - i))
        bed[i: i + n] += s[:n]
    sf.write(music_wav, bed, sr)
    print(f"sfx: {len(spec['sfx'])} sounds")


def build(path, skip_vo=False, still=None, frames=None):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    sid = spec["id"]
    out = HERE / "out" / sid
    pub = HERE / "public" / sid
    out.mkdir(parents=True, exist_ok=True)
    pub.mkdir(parents=True, exist_ok=True)
    wav = pub / "vo.wav"
    tj = out / "timing.json"
    if not (skip_vo and wav.exists() and tj.exists()):
        words = voice(spec, wav)
        toks = tokens(spec["vo"])
        times = align(toks, words) if words else timings(spec, wav)[1]
        tj.write_text(json.dumps([toks, times]))
    toks, times = json.loads(tj.read_text())
    vo_len = sf.info(str(wav)).duration
    total = round((vo_len + spec.get("tail", 0.25)) * FPS)  # ends just after the last word, so it loops
    keys = [norm(t) for t in toks]

    def frame(at):
        if at == "end":
            return total
        if isinstance(at, (int, float)):
            return round(at * FPS)
        m = re.fullmatch(r"([^#+-]+)(?:#(\d+))?([+-][\d.]+)?", at)
        if not m:
            raise SystemExit(f"bad cue {at!r}")
        word, n, off = norm(m[1]), int(m[2] or 1), float(m[3] or 0)
        hits = [i for i, k in enumerate(keys) if k == word]
        if len(hits) < n:
            raise SystemExit(f"cue {at!r}: the word {word!r} is spoken {len(hits)} time(s) in the vo")
        return round((times[hits[n - 1]][0] + off) * FPS)

    def normed(rel):
        """YouTube serves AV1/VP9 and variable frame rates; normalise each source once to constant-frame-rate H.264
        at its NATIVE rate. (Forcing 30 fps here duplicated every 5th frame of 25 fps broadcasts: visible judder.
        The 25->30 conversion happens per segment, by motion interpolation.)"""
        raw = HERE / rel
        dst = pub / f"src_{Path(rel).stem}_cfr.mp4"
        if not dst.exists() or dst.stat().st_mtime < raw.stat().st_mtime:
            subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(raw), "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "fast", "-crf", "14",
                            "-pix_fmt", "yuv420p", "-g", "10", "-an", str(dst)], check=True)
        return dst

    def retime(r, d):
        """setpts expression mapping source time to output time. A constant rate divides; a ramp a->b over d
        output seconds plays source s(t) = a*t + k*t^2 (k = (b-a)/2d), so output t = (-a + sqrt(a^2 + 4ks)) / 2k."""
        if not isinstance(r, list) or abs(r[1] - r[0]) < 1e-6:
            return f"(PTS-STARTPTS)/{r[0] if isinstance(r, list) else r}"
        a, k = r[0], (r[1] - r[0]) / (2 * d)
        # clamp to the source the segment uses: past it a slowing ramp's sqrt goes negative (NaN timestamps), and
        # stray frames from the end of the file leaked into the last frame, the one a `freeze: here` holds
        s = f"min((PTS-STARTPTS)*TB\\,{a * d + k * d * d:.6f})"
        return f"((-{a}+sqrt({a}*{a}+4*({k})*{s}))/(2*({k})))/TB"

    def fps_of(path):
        num, den = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                                   "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.strip().split("/")
        return float(num) / float(den)

    # The spec's source is the primary clip and sets the frame size. A timeline segment may take its picture from
    # another clip ("src": "sources/001/src.mp4"); it is lanczos-scaled to the primary size, so overlay positions
    # (fractions of the frame) keep working as long as both clips are 16:9.
    src = normed(spec["source"]["file"])
    w, h = map(int, subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                                    "-of", "csv=p=0", str(src)], capture_output=True, text=True, check=True).stdout.strip().split(","))

    det = subprocess.run([FFMPEG, "-hide_banner", "-ss", "5", "-t", "6", "-i", str(src), "-vf", "cropdetect=24:2:0", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    crop_t = crop_b = 0.0
    if "crop=" in det:
        cw, ch, cx, cy = map(int, det.rsplit("crop=", 1)[1].split()[0].split(":"))
        if cw >= w * 0.98:  # only horizontal strips (top/bottom); a narrow detection means a dark shot, not bars
            crop_t, crop_b = cy / h, (h - cy - ch) / h
            if crop_t > 0.13 or crop_b > 0.13:
                crop_t = crop_b = 0.0

    segments, start, here, here_src = [], 0, 0.0, src
    for i, g in enumerate(spec["timeline"]):
        cont = g.get("freeze") == "here" or g.get("play") == "here"  # continue exactly where the previous play stopped
        seg_src = normed(g["src"]) if "src" in g else (here_src if cont else src)
        if g.get("play") == "here":
            g = dict(g, play=round(here, 3))
        end = frame(g["until"])
        if end <= start:
            raise SystemExit(f"timeline segment {i} ends at {end} but starts at {start} - check its 'until' cue")
        seg = {"f": start, "dur": end - start}
        if "freeze" in g:
            if g["freeze"] == "here":  # hold the last frame the previous play segment reached
                g = dict(g, freeze=round(here - 1 / 30, 3))
                seg["here"] = True  # its still is taken from the rendered previous part, so it matches exactly
            still_file = pub / f"freeze_{i}.jpg"
            subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-ss", str(g["freeze"]), "-i", str(seg_src), "-frames:v", "1", "-q:v", "2",
                            str(still_file)], check=True)
            seg |= {"kind": "freeze", "t": g["freeze"], "still": f"{sid}/{still_file.name}"}
        else:
            r = g.get("rate", 1)
            seg |= {"kind": "play", "t": g["play"], "rate": r, "smooth": g.get("smooth", True)}
            mean_rate = (r[0] + r[1]) / 2 if isinstance(r, list) else r  # a linear speed ramp covers its mean rate
            here, here_src = g["play"] + mean_rate * (end - start) / FPS, seg_src
        # blurred backdrop for frames wider than the source: one still per segment. A second <OffthreadVideo> on the
        # same file makes Remotion's compositor fail mid-render ("No frame found at position"), so no live copy.
        bg = pub / f"bg_{i}.jpg"
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-ss", str(seg["t"]), "-i", str(seg_src), "-frames:v", "1",
                        "-vf", "scale=320:-2,boxblur=8:2", "-q:v", "4", str(bg)], check=True)
        seg["bg"] = f"{sid}/{bg.name}"
        seg["_src"] = str(seg_src)
        segments.append(seg)
        start = end
    if start != total:
        raise SystemExit(f"the timeline ends at frame {start}, the voice at {total} - make the last segment 'until': 'end'")

    # The picture track is assembled here, not in Remotion: slow-mo retimed, freezes held, all concatenated into
    # one base.mp4 of exactly `total` frames. Remotion then plays it straight through and only draws on top.
    # (Remotion's own seeking at odd playback rates failed mid-render on this laptop: "No frame found at position".)
    enc = ["-c:v", "libx264", "-preset", "fast", "-crf", "14", "-pix_fmt", "yuv420p", "-r", "30", "-an"]
    # Parts are cached by everything that shapes them, so a revision round only re-renders the segments it changed
    # (a full rebuild with motion interpolation takes ~16 min on this laptop).
    cache = out / "cache"
    cache.mkdir(exist_ok=True)
    parts = []
    for i, seg in enumerate(segments):
        if seg["kind"] == "play":
            # every segment that isn't already 30 fps at 1x is motion-interpolated to 30 fps: slow motion gets real
            # in-between frames, and 25 fps broadcasts don't judder. "smooth": false opts out (fast pans, flashes).
            r = seg["rate"]
            ramp = isinstance(r, list)
            needs = ramp or r < 0.99 or abs(fps_of(seg["_src"]) - 30) > 0.01
            interp = ",minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1" if needs and seg.get("smooth", True) else ",fps=30"
            cmd = ["-ss", str(seg["t"]), "-i", seg["_src"], "-vf",
                   f"setpts={retime(r, seg['dur'] / FPS)}{interp},scale={w}:{h}:flags=lanczos,setsar=1,tpad=stop_mode=clone:stop_duration=10"]
            ident = [cmd, Path(seg["_src"]).stat().st_mtime]
        else:
            still_path = HERE / "public" / seg["still"]
            if seg.get("here") and parts:
                n = segments[i - 1]["dur"] - 1
                subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(parts[-1]), "-vf", f"select=eq(n\\,{n})", "-fps_mode", "passthrough",
                                "-frames:v", "1", "-q:v", "2", str(still_path)], check=True)
            cmd = ["-loop", "1", "-framerate", "30", "-i", str(still_path), "-vf", f"scale={w}:{h}:flags=lanczos,setsar=1"]
            ident = [cmd, hashlib.sha1(still_path.read_bytes()).hexdigest()]
        key = hashlib.sha1(json.dumps([ident, seg["dur"], enc]).encode()).hexdigest()[:16]
        part = cache / f"{key}.mp4"
        if not part.exists():  # write-then-rename: a crash mid-encode must not leave a broken part that looks cached
            tmp = part.with_name(f"{key}.tmp.mp4")
            # 2 threads: minterpolate at full thread count starved this 3.8 GB laptop and crashed Windows (2026-10-20)
            subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-filter_threads", "1", *cmd, "-frames:v", str(seg["dur"]), *enc,
                            "-threads", "2", str(tmp)], check=True)
            tmp.replace(part)
        parts.append(part)
    (out / "parts.txt").write_text("".join(f"file 'cache/{q.name}'\n" for q in parts))
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(out / "parts.txt"), "-c", "copy",
                    str(pub / "base.mp4")], check=True)
    used = {q.name for q in parts}
    for q in cache.glob("*.mp4"):  # keep the cache to this cut's parts
        if q.name not in used:
            q.unlink()
    for seg in segments:
        seg.pop("_src")
    # ...and handed to Remotion as numbered JPEGs, not a video: its video decoder runs out of cache on this
    # laptop even on straight playback ("No frame found at position"). Images can't fail that way.
    fdir = pub / "frames"
    shutil.rmtree(fdir, ignore_errors=True)
    fdir.mkdir()
    # finishing pass baked into every frame: lanczos upscale towards the 1920 px output height (Chrome would only do a
    # soft bilinear one), light sharpening, and the house grade (a touch more contrast and saturation)
    oh = max(h, 1440)  # ponytail: 2560x1440 frames is what this laptop proved it can render; Chrome does the last 1.33x to 1920
    presets = {
        # gentle teal/orange split, a touch of contrast, fine film grain: "cinematic" without looking filtered
        "cinematic": "eq=contrast=1.07:saturation=1.08:brightness=-0.005,"
                     "colorbalance=rs=-0.03:gs=-0.005:bs=0.04:rh=0.04:gh=0.01:bh=-0.03,noise=alls=3:allf=t",
        "clean": "eq=contrast=1.05:saturation=1.12:brightness=0.01",
    }
    grade = presets.get(spec.get("grade", "cinematic"), spec.get("grade", "cinematic"))
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(pub / "base.mp4"), "-vf",
                    f"scale=-2:{oh}:flags=lanczos,unsharp=5:5:{0.5 if oh > h else 0.3},{grade}",
                    "-q:v", "2", str(fdir / "%04d.jpg")], check=True)
    if len(list(fdir.glob("*.jpg"))) != total:
        raise SystemExit(f"base.mp4 has {len(list(fdir.glob('*.jpg')))} frames, expected {total}")

    beats = []
    for b in spec["beats"]:
        b = dict(b)
        b["f"] = frame(b.pop("at"))
        if b["do"] in GRAPHICS:  # graphics are up just before the action they mark, never after (003 hook ring was late)
            b["f"] = max(0, b["f"] - round(b.pop("lead", spec.get("graphics_lead", 0.1)) * FPS))
        if "until" in b:
            b["until"] = frame(b["until"])
        if "dur" in b:
            b["dur"] = round(b["dur"] * FPS)
        beats.append(b)
    beats.sort(key=lambda b: b["f"])

    if spec.get("music"):
        score(pub, spec, total / FPS + 0.5, wav, lambda c: frame(c) / FPS)
        music = f"{sid}/music.wav"
    else:
        pad(pub, total / FPS + 1, wav)
        music = f"{sid}/pad.wav"
    if spec.get("sfx"):
        sfx(HERE / "public" / music, spec, wav, lambda c: frame(c) / FPS)
    props = {
        "durationInFrames": total, "frames": f"{sid}/frames", "srcW": w, "srcH": h, "segments": segments, "beats": beats,
        "captions": [[round(s * FPS), max(round(s * FPS) + 4, round(e * FPS)), tok] for tok, (s, e) in zip(toks, times)],
        "capY": spec.get("caption_y", 0.72), "loopFrames": round(spec.get("loop", 0.3) * FPS), "vo": f"{sid}/vo.wav", "music": music, "musicVol": spec.get("music_vol", 1),
        "cropT": crop_t, "cropB": crop_b,
    }
    pj = out / "props.json"
    pj.write_text(json.dumps(props), encoding="utf-8")
    print(f"{sid}: source {w}x{h}, vo {vo_len:.1f}s, {total} frames, {len(segments)} segments, {len(beats)} beats")

    if still is not None:
        subprocess.run([NPX, "remotion", "still", "src/index.ts", "ClipShort", str(out / f"still_{still}.png"),
                        f"--props={pj}", f"--frame={still}", "--scale=0.5"], cwd=HERE, check=True)
        return
    raw = out / "raw.mp4"
    cmd = [NPX, "remotion", "render", "src/index.ts", "ClipShort", str(raw), f"--props={pj}", f"--concurrency={2 if WIN else 4}", "--crf=18"]
    if frames:
        cmd.append(f"--frames={frames}")
    subprocess.run(cmd, cwd=HERE, check=True)
    final = out / f"{sid}.mp4"
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(raw), "-t", f"{total / FPS:.3f}", "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-2.0:LRA=7",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(final)], check=True)
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", str(final), "-vf", "fps=1,scale=216:-1,tile=8x4:padding=4",
                    "-frames:v", "1", str(out / "sheet.jpg")], check=True)
    print(f"done -> {final}")


if __name__ == "__main__":
    a = sys.argv[1:]
    opt = lambda k: next((x.split("=", 1)[1] for x in a if x.startswith(f"--{k}=")), None)
    build(next(x for x in a if not x.startswith("--")), skip_vo="--skip-vo" in a,
          still=int(opt("still")) if opt("still") else None, frames=opt("frames"))
