"""Series Shorts from a short card: the cheap path to 2 a day. The card names 3-4 moments (clip, shot, key instant,
one line each); this writes the voice and the whole spec (cut, freezes, graphics, music, sfx), so no editor or motion
agent is needed. See SERIES.md for the card format.

  uv run --no-project --with edge-tts==7.3.1 --with kokoro-onnx==0.4.7 --with faster-whisper==1.2.1 --with soundfile --with numpy python series.py series/<id>.json

Writes specs/<id>.json plus public/<id>/vo.wav and out/<id>/timing.json (cloud.py ships those, so the cloud render uses
this exact voice). Then: `python build.py specs/<id>.json --skip-vo --still=F` to look, `python cloud.py <id>` to render.
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

import build

HERE = Path(__file__).parent
WPS = (3.4, 3.55)  # user, 2026-11-01: 3.82 'the pace of the voice over is fast'; v1's 3.41 voice was 'good'

SERIES = {
    # countdown of blunders; red = the mistake
    "0iq": {"intro": ["Number three.", "Number two.", "And number one."], "card": ["#3", "#2", "#1"], "color": "#EB2B28",
            "music": "Hitman"},
    # three balls that build the trap, then the wicket; yellow = the clever thing
    "setups": {"intro": ["Ball one.", "Ball two.", "Ball three.", "Then,"], "card": ["BALL 1", "BALL 2", "BALL 3", "WICKET"],
               "color": "#FEE300", "music": "Impact Prelude"},
    # one moment told beat by beat (the classic 1000 IQ Short): each "moment" is one line, no counter cards
    "one": {"intro": [""] * 8, "card": [""] * 8, "color": "#FEE300", "music": "Impact Prelude"},
}
SFX = {  # diegetic only, on the moment's key instant (knowledge/voice.md F.0)
    "stumps": [{"s": "wooden-baseball-bat-hit-02", "db": -9}, {"s": "impact-wood-knock-03", "db": -15, "len": 0.8}],
    "bat": [{"s": "wooden-baseball-bat-hit-03", "db": -11, "len": 0.8}],
    "catch": [{"s": "impact-wood-knock-03", "db": -18, "len": 0.5}],
}


def lines(card, speed):
    cfg = SERIES[card["series"]]
    out = [{"text": card["hook"], "speed": round(1.13 * speed, 3), "pause": 0.1}]
    for intro, m in zip(cfg["intro"], card["moments"]):
        out.append({"text": f"{intro} {m['say']}".strip(), "speed": round(1.15 * speed, 3), "pause": 0.1})  # hits: no gap >= 0.3 s
    out.append({"text": card["outro"], "speed": round(1.1 * speed, 3), "pause": 0})
    return out


def voice(card, spec, pub, out):
    """Voice the lines; one retake at a corrected speed if the pace is outside the house range."""
    if card.get("vo_file"):  # a finished take made outside (Gemini in AI Studio): no synthesis, same chain + Whisper
        spec["vo_lines"] = lines(card, 1.0)
        spec["vo"] = " ".join(x["text"] for x in spec["vo_lines"])
        a, sr = sf.read(str(HERE / card["vo_file"]), dtype="float32")
        if sr != build.SR or a.ndim != 1:
            raise SystemExit(f"{card['vo_file']}: want {build.SR} Hz mono, got {sr} Hz, {a.ndim} channel dim(s)")
        on = np.flatnonzero(np.abs(a) > 0.01)  # trim its own silence so the hook starts on frame 1
        build.chain(a[max(0, on[0] - int(build.LEAD * sr)): on[-1] + int(0.06 * sr)], pub / "vo.wav")
        toks, times = build.timings(spec, pub / "vo.wav")
        wps = len(toks) / sf.info(str(pub / "vo.wav")).duration
        print(f"voice file {card['vo_file']}: {len(toks)} words, {wps:.2f} words/s")
        if not 3.3 <= wps <= 3.65:  # check.py's range; a file can't be re-paced here (no time-stretching)
            raise SystemExit(f"{wps:.2f} words/s is outside 3.3-3.65: retake it with a different Style")
        (out / "timing.json").write_text(json.dumps([toks, times]))
        return toks, times
    speed, tried = 1.0, []
    for i in range(6):
        if i == 5:  # no take landed in range: redo the closest one (Kokoro is deterministic per speed)
            speed = min(tried)[1]
        spec["vo_lines"] = lines(card, speed)
        spec["vo"] = " ".join(x["text"] for x in spec["vo_lines"])
        words = build.voice(spec, pub / "vo.wav")  # None for a Kokoro voice: Whisper hears the word times back
        toks, times = build.timings(spec, pub / "vo.wav") if words is None else (build.tokens(spec["vo"]), None)
        times = times or build.align(toks, words)
        wps = len(toks) / sf.info(str(pub / "vo.wav")).duration
        print(f"take at speed {speed:.3f}: {wps:.2f} words/s")
        if WPS[0] <= wps <= WPS[1] or i == 5:
            break
        tried.append((abs(wps - sum(WPS) / 2), speed, wps))
        goal = sum(WPS) / 2
        lo = [x for x in tried if x[2] < goal]
        hi = [x for x in tried if x[2] > goal]
        if lo and hi:  # Kokoro's pace isn't proportional to speed: interpolate between the bracketing takes
            (_, s0, w0), (_, s1, w1) = max(lo, key=lambda x: x[2]), min(hi, key=lambda x: x[2])
            speed = s0 + (goal - w0) * (s1 - s0) / (w1 - w0)
        else:
            speed *= goal / wps
    (out / "timing.json").write_text(json.dumps([toks, times]))
    print(f"voice: {len(toks)} words, {wps:.2f} words/s")
    return toks, times


def moment(m, t0, t1, color, hit_at=None):
    """Timeline + beats for one key shot over output seconds [t0, t1): live-ish run-up to the key instant, a freeze on it
    carrying the graphics (static picture, so positions read off the frame at `hit` are exact), then the aftermath slow.
    hit_at: the output time the key instant should land on (the word that names it), else 60% of the way in."""
    a, b = m["shot"]
    h = m["hit"]
    d = t1 - t0
    hold = m.get("hold", min(0.9, max(0.5, 0.18 * d)))  # a card can hold a key freeze longer (the payoff frame)
    pre = hit_at - t0 if hit_at and 0.3 < hit_at - t0 < d - hold else (d - hold) * 0.6
    r1 = (h - a) / pre
    if r1 > 1:  # more run-up than time: start later in the shot
        a, r1 = h - pre, 1.0
    elif r1 < 0.25:  # too little run-up: play it at 0.25 and give the rest to the aftermath
        pre = (h - a) / 0.25
        r1 = 0.25
    hit_t = t0 + pre
    post = t1 - hit_t - hold
    if post < 0.1:  # a sliver of aftermath is a zero-frame segment: the freeze takes it
        hold, post = t1 - hit_t - 0.1, 0.1
    r2 = min(1.0, (b - h) / post) if post > 0 else 0
    if r2 < 0.2:  # aftermath too short to fill: hold the freeze longer instead
        hold, post, r2 = t1 - hit_t - 0.4, 0.4, min(1.0, (b - h) / 0.4)
    tl = [{"src": m["src"], "play": round(a, 3), "rate": round(r1, 3), "until": round(hit_t, 3)},
          {"freeze": "here", "until": round(hit_t + hold, 3)},
          {"play": "here", "rate": round(r2, 3), "until": round(t1, 3)}]
    f = t0 + 0.02
    beats = [{"at": round(f, 3), "do": "frame", "h": m.get("h", 1), "cx": m.get("cx", 0.5), "cy": 0.5, "dur": 0}]
    if "on" in m:
        beats.append({"at": round(hit_t, 3), "do": "ring", "lead": 0, "on": m["on"], "r": 0.08, "squash": m.get("squash", 0.8),
                      "color": color, "until": round(hit_t + hold, 3)})
        if "label" in m:  # staggered after the ring (0.3 s) so circle and label each get their own beat and sound
            lx, ly = m["on"]
            beats.append({"at": round(hit_t + 0.3, 3), "do": "label", "lead": 0, "on": m.get("label_on", [lx, min(0.9, ly + 0.2)]), "text": m["label"],
                          "color": color, "size": 64, "bg": True, "until": round(hit_t + hold, 3)})
    if "who" in m:
        beats.append({"at": round(hit_t, 3), "do": "tag", "lead": 0, "on": m["who_at"], "text": m["who"], "size": 60, "bg": True,
                      "until": round(hit_t + hold, 3)})
    sfx = [dict(x, at=round(hit_t, 3)) for x in SFX.get(m.get("sfx"), [])]
    # pointing sounds (user, 2026-11-13, reference Short P4e7XbHtux4): one sound per graphic as it appears, never on a cut.
    # Levels are peak vs the voice's peaks ("pk"). earshot measured the reference's pops at a median 14 dB over their bed;
    # RMS-levelled sounds came out 3-7 dB over (masked by the voice), the bright ding cut through best (10.8).
    # After a diegetic hit (stumps, bat) the ring waits 0.12 s so the two stay apart.
    if "on" in m:
        sfx.append({"s": "ui-ring", "pk": 0, "at": round(hit_t + (0.12 if sfx else 0.03), 3)})
        if "label" in m:
            sfx.append({"s": "ui-pop", "pk": 0, "at": round(hit_t + 0.3, 3)})
    if "arrow" in m and hold >= 0.8:  # [[x, y] from, [x, y] to] on the hit frame, optional "bend"; needs room after the label
        a = round(hit_t + 0.55, 3)
        beats.append({"at": a, "do": "arrow", "lead": 0, "from": m["arrow"][0], "to": m["arrow"][1], "bend": m.get("bend", 0.04),
                      "dur": 0.3, "until": round(hit_t + hold, 3)})  # seconds (build.py multiplies by FPS): 9 here meant a 9 s draw, a stub on 028 and 029
        sfx.append({"s": "ui-arrow", "pk": 0, "at": a})
    return tl, beats, sfx, hit_t


def main(card_path):
    card = json.loads(Path(card_path).read_text(encoding="utf-8"))
    cfg = SERIES[card["series"]]
    ms = card["moments"]
    assert len(ms) == len(cfg["intro"]) or (card["series"] == "0iq" and len(ms) == 3) or card["series"] == "one",         f"{card['series']} wants {len(cfg['intro'])} moments"
    sid = card["id"]
    out, pub = HERE / "out" / sid, HERE / "public" / sid
    out.mkdir(parents=True, exist_ok=True)
    pub.mkdir(parents=True, exist_ok=True)
    spec = {"id": sid, "series": card["series"], "part": card.get("part", 1), "title": card["title"], "topic": card.get("topic", sid),
            "hook_card": " ".join(card["hook_card"]), "fact_check": card["fact_check"],
            "source": {"url": ms[0].get("url", ""), "channel": ms[0].get("channel", ""), "file": ms[0]["src"]},
            "sources": [{k: m.get(k) for k in ("url", "channel", "src")} for m in ms], "voice": card.get("voice", "bm_george")}  # George: the voice of the 691K Ashwin Short
    toks, times = voice(card, spec, pub, out)
    # line i starts at the first word of line i (word counts are exact: the lines are joined into vo)
    starts, n = [], 0
    for x in spec["vo_lines"]:
        starts.append(times[n][0])
        n += len(build.tokens(x["text"]))
    end = round((sf.info(str(pub / "vo.wav")).duration + 0.25) * build.FPS) / build.FPS
    bounds = [s - 0.05 for s in starts[1:len(ms) + 1]] + [end]
    timeline, beats, sfx, hits = [], [], [], []
    # hook: the last key shot (the payoff), live into slow, under the hook card (the house frame-0 rule)
    beats.append({"at": 0, "do": "captions", "y": card.get("caption_y", 0.86)})
    if "hook_shots" not in card:  # a card's hook_shots run through the moment loop below, like any line
        last = [{"src": m["src"], **x} for m in ms for x in m.get("shots", [m]) if "hit" in x][-1]
        used = 0.5 * bounds[0]  # source seconds a 0.7 -> 0.3 ramp eats; must stay inside the shot
        timeline.append({"src": last["src"], "play": round(max(last["shot"][0], min(last["hit"] - 1.0, last["shot"][1] - used)), 3), "rate": [0.7, 0.3],
                         "until": round(bounds[0], 3)})
        beats.append({"at": 0, "do": "frame", "h": last.get("h", 1), "cx": last.get("cx", 0.5), "cy": 0.5, "dur": 0})
    for i, word in enumerate(card["hook_card"][:2]):
        beats.append({"at": 0, "do": "text", "lead": 0, "text": word, "y": 0.115 + 0.07 * i, "color": cfg["color"] if i else "#FFFFFF",
                      "size": 96, "until": round(bounds[0] - 0.2, 3)})
    hook = [(-1, {"src": card["hook_shots"][0]["src"], "shots": card["hook_shots"]})] if "hook_shots" in card else []
    for k, m in hook + list(enumerate(ms)):
        t0, t1 = (0.0, bounds[0]) if k < 0 else (bounds[k], bounds[k + 1])
        n0 = sum(len(build.tokens(x["text"])) for x in spec["vo_lines"][:k + 1])  # first token of this moment's line
        line = [w.strip(".,?!'\"").lower() for w in build.tokens(spec["vo_lines"][k + 1]["text"])]
        at = lambda word: times[n0 + line.index(word.lower())][0]  # when the narrator says `word` (first time in the line)
        shots = [{"src": m["src"], **x} for x in m["shots"]] if "shots" in m else [m]  # each event on screen while named
        hit_t = None
        cuts = [t0] + [round(at(x["from"]) - 0.05, 3) for x in shots[1:]] + [t1]
        for j, x in enumerate(shots):
            s0, s1 = cuts[j], cuts[j + 1]
            if "hit" in x:
                tl, bt, sx, hit_t = moment(x, s0, s1, cfg["color"], at(x["hit_at"]) if "hit_at" in x else None)
                timeline += tl
                beats += bt
                sfx += sx
            else:  # a plain shot: the card's rate, which must keep play + rate * length inside one camera shot
                timeline.append({"src": x["src"], "play": x["play"], "rate": x.get("rate", 1), "until": round(s1, 3)})
                beats.append({"at": round(s0 + 0.02, 3), "do": "frame", "h": x.get("h", 1), "cx": x.get("cx", 0.5), "cy": 0.5, "dur": 0})
                for i, line in enumerate(x.get("text", [])):  # big on-screen fact over live play (a law, a margin), not a tiny source card
                    beats.append({"at": round(s0 + 0.02, 3), "do": "text", "lead": 0, "text": line, "y": 0.115 + 0.07 * i,
                                  "color": cfg["color"] if i else "#FFFFFF", "size": 96, "until": round(s1 - 0.05, 3)})
        if hit_t is None:
            continue
        hits.append(hit_t)
        if k >= 0 and cfg["card"][k]:
            beats.append({"at": round(t0 + 0.02, 3), "do": "text", "lead": 0, "text": cfg["card"][k], "y": 0.115, "color": cfg["color"],
                          "size": 110, "until": round(min(t0 + 1.4, hit_t - 0.05), 3)})
    timeline[-1]["until"] = "end"
    beats.sort(key=lambda b: b["at"] if isinstance(b["at"], (int, float)) else 0)
    # a freeze that doesn't fit its line gives segments that run backwards (022 stumps, 028 OUT): fail here, not in the render
    ends = [x["until"] for x in timeline if x["until"] != "end"]
    bad = [f"{a} -> {b}" for a, b in zip(ends, ends[1:]) if b <= a]
    if bad:
        raise SystemExit(f"{sid}: timeline runs backwards at {', '.join(bad)} s: that line is too short for its freeze "
                         "(move hit_at to an earlier word, shorten hold, or lengthen the sentence)")
    track = card.get("music", cfg["music"])
    lib = json.loads((HERE / "music" / "library.json").read_text())[track]
    spec |= {
        "timeline": timeline, "beats": beats, "sfx": sfx, "caption_y": card.get("caption_y", 0.86),  # up off a broadcast score bar
        "loop": 0.3, "tail": 0.25,
        "grade": "eq=contrast=1.14:saturation=1.06:brightness=-0.055:gamma=0.9,colorbalance=rs=-0.05:gs=-0.01:bs=0.06:rh=0.05:gh=0.01:bh=-0.04,noise=alls=4:allf=t",
        # the first track hit late enough that the music is already playing at frame 0 (an earlier hit = silence up front)
        "music": {"track": track, "hit": next((h for h in lib["hits"] if h >= hits[-1]), lib["duration"] * 0.6), "on": round(hits[-1], 3), "db": -16,
                  "moves": [{"at": round(hits[-1] - 0.6, 3), "db": -26, "fade": 0.4}, {"at": round(hits[-1], 3), "db": -11, "fade": 0.08},
                            {"at": round(hits[-1] + 1.2, 3), "db": -18, "fade": 0.8}], "fade_out": 0.3},
        "music_credit": f"\"{track}\" Kevin MacLeod (incompetech.com), Licensed under Creative Commons: By Attribution 4.0 License, http://creativecommons.org/licenses/by/4.0/",
    }
    (HERE / "specs" / f"{sid}.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{sid}: {end:.1f} s, {len(timeline)} segments, {len(beats)} beats, moments at " + ", ".join(f"{h:.2f}" for h in hits))


if __name__ == "__main__":
    main(sys.argv[1])
