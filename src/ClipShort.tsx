import React from "react";
import { AbsoluteFill, Audio, Easing, Img, cancelRender, continueRender, delayRender, interpolate, staticFile, useCurrentFrame } from "remotion";

// The motion/VFX layer ("the After Effects pass"). The picture is base footage baked by build.py (slow-mo
// interpolated, upscaled, graded) and shown one JPEG per output frame. Everything here sits on top of it:
// camera moves, freeze look, telestrator graphics, captions. House style measured off
// @footymemology (research/01-format-forensics.md). Times are output frames; overlay positions are fractions of
// the SOURCE frame, so graphics stay on the player through any zoom.

// Segments map output frames to source time (for tracked graphics) and mark freezes for the motion layer.
// rate: a number, or [a, b] = a linear speed ramp from a to b across the segment
export type Seg = { f: number; dur: number; kind: "play" | "freeze"; t: number; rate?: number | [number, number]; still?: string; bg?: string };
export type Beat = { f: number; until?: number; do: string; [k: string]: unknown };
export type Props = {
  durationInFrames: number;
  frames: string;
  loopFrames?: number; // dissolve into frame 0 over the last N frames (default 9 = 0.3 s; 0 = off)
  srcW: number;
  srcH: number;
  segments: Seg[];
  beats: Beat[];
  captions: [number, number, string][];
  capY?: number;
  vo?: string;
  music?: string;
  musicVol?: number;
  cropT?: number; // black strips detected on the source (fractions of its height), kept out of frame
  cropB?: number;
};

export const W = 1080;
export const H = 1920; // 9:16 since 003 (001-002 were 3:4 at 1440)
const YELLOW = "#FEE300";
const GREEN = "#04DA07";
const RED = "#EB2B28";
const IN = 8; // frames for a graphic to draw on
const OUT = 5; // frames for it to leave
const ease = Easing.bezier(0.45, 0, 0.2, 1);
const easeOut = Easing.bezier(0.16, 1, 0.3, 1);
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const FONT = "Mont, 'Arial Black', Arial, sans-serif";
const shadow = "0 3px 10px rgba(0,0,0,0.65), 0 1px 2px rgba(0,0,0,0.9)";

// The house font (Montserrat, OFL) from public/fonts; the render waits until it is loaded.
const fontHandle = delayRender("Loading Montserrat");
Promise.all([
  new FontFace("Mont", `url(${staticFile("fonts/Montserrat.ttf")})`, { weight: "100 900" }).load(),
  new FontFace("Mont", `url(${staticFile("fonts/Montserrat-Italic.ttf")})`, { weight: "100 900", style: "italic" }).load(),
])
  .then((fs) => {
    fs.forEach((f) => document.fonts.add(f));
    continueRender(fontHandle);
  })
  .catch((e) => cancelRender(e));

type View = { cx: number; cy: number; h: number };

// h = window height / source height. 1 = full-height crop (house look), <1 = punch in, "fit" = whole frame across.
// `frame` beats set the window; a `follow` beat makes the window track a player (smoothed over `lag` frames, like a
// camera operator easing after him) until its `until`, the next frame/follow beat, then eases back over 10 frames.
const viewAt = (beats: Beat[], frame: number, fitH: number, segs: Seg[]): View => {
  let v: View = { cx: 0.5, cy: 0.5, h: 1 };
  for (const b of beats) {
    if (b.do !== "frame" || b.f > frame) continue;
    const to: View = { cx: (b.cx as number) ?? v.cx, cy: (b.cy as number) ?? v.cy, h: b.h === "fit" ? fitH : ((b.h as number) ?? v.h) };
    const dur = (b.dur as number) ?? 12;
    const t = dur <= 0 ? 1 : ease(clamp01((frame - b.f) / dur));
    v = { cx: v.cx + (to.cx - v.cx) * t, cy: v.cy + (to.cy - v.cy) * t, h: v.h + (to.h - v.h) * t };
  }
  const fol = beats.filter((b) => b.do === "follow" && b.f <= frame).pop();
  if (!fol) return v;
  const next = beats.find((b) => (b.do === "frame" || b.do === "follow") && b.f > fol.f);
  const end = fol.until ?? next?.f ?? Infinity;
  if (frame >= end + 10) return v;
  const lag = (fol.lag as number) ?? 8;
  let sx = 0;
  let sy = 0;
  for (let j = 0; j <= lag; j++) {
    const fr = Math.min(Math.max(fol.f, frame - j), end - 1);
    const q = pointAt(fol.on, srcTime(segs, fr));
    sx += q[0];
    sy += q[1];
  }
  const to: View = { cx: sx / (lag + 1) + ((fol.dx as number) ?? 0), cy: sy / (lag + 1) + ((fol.dy as number) ?? 0), h: (fol.h as number) ?? v.h };
  const w = (fol.f === 0 ? 1 : ease(clamp01((frame - fol.f) / ((fol.dur as number) ?? 10)))) * (frame < end ? 1 : 1 - ease(clamp01((frame - end) / 10)));
  return { cx: v.cx + (to.cx - v.cx) * w, cy: v.cy + (to.cy - v.cy) * w, h: v.h + (to.h - v.h) * w };
};

const segIndex = (segs: Seg[], frame: number) => Math.max(0, segs.findIndex((s) => frame >= s.f && frame < s.f + s.dur));
const srcTime = (segs: Seg[], frame: number) => {
  const s = segs[segIndex(segs, frame)];
  if (s.kind === "freeze") return s.t;
  const x = (frame - s.f) / 30;
  const r = s.rate ?? 1;
  if (!Array.isArray(r)) return s.t + r * x;
  const k = (r[1] - r[0]) / (2 * (s.dur / 30)); // ramp a->b: s(x) = a*x + k*x^2
  return s.t + r[0] * x + k * x * x;
};

/** Point at a source time: fixed [x,y], or tracked [[t,x,y],...] keyframes joined by a Catmull-Rom curve (no kinks). */
const pointAt = (p: unknown, t: number): [number, number] => {
  const a = p as number[] | number[][];
  if (typeof a[0] === "number") return a as [number, number];
  const k = a as number[][];
  if (t <= k[0][0]) return [k[0][1], k[0][2]];
  if (t >= k[k.length - 1][0]) return [k[k.length - 1][1], k[k.length - 1][2]];
  let i = 1;
  while (t > k[i][0]) i++;
  const p0 = k[Math.max(0, i - 2)];
  const p1 = k[i - 1];
  const p2 = k[i];
  const p3 = k[Math.min(k.length - 1, i + 1)];
  const u = (t - p1[0]) / (p2[0] - p1[0]);
  const cr = (j: number) =>
    0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * u + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * u * u + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * u * u * u);
  return [cr(1), cr(2)];
};

export const ClipShort: React.FC<Props> = (p) => {
  const frame = useCurrentFrame();
  const fitH = (H * p.srcW) / (W * p.srcH);
  const v = viewAt(p.beats, frame, fitH, p.segments);
  const vPrev = frame > 0 ? viewAt(p.beats, frame - 1, fitH, p.segments) : v; // frame 0 has no previous view: no false motion blur
  const si = segIndex(p.segments, frame);
  const seg = p.segments[si];
  const age = frame - seg.f;
  // Freeze look: an eased punch-in and a deeper vignette that build over the freeze and ease back out over
  // 10 frames after it (snapping off on release read as a jump). Cuts between shots are hard cuts, as on the
  // reference channel: a zoom-blur transition made every cut open on a blurred frame.
  const lastFreeze = [...p.segments].reverse().find((g) => g.kind === "freeze" && g.f <= frame);
  const freezeAmt = (inFrames: number) => {
    if (!lastFreeze) return 0;
    const end = lastFreeze.f + lastFreeze.dur;
    if (frame < end) return easeOut(clamp01((frame - lastFreeze.f) / inFrames));
    return easeOut(clamp01(lastFreeze.dur / inFrames)) * (1 - ease(clamp01((frame - end) / 10)));
  };
  // camera life: a slow push through every play segment, plus the freeze punch-in
  // (no separate per-segment push: it reset at every freeze boundary and snapped the zoom by 2.5%)
  const life = 1 + 0.045 * freezeAmt(24);
  const top0 = (p.cropT ?? 0) * p.srcH;
  const bot0 = (1 - (p.cropB ?? 0)) * p.srcH;
  const place = (v: View, life: number) => {
    let s = (H / (v.h * p.srcH)) * life;
    if (H / s > bot0 - top0 && H / s <= p.srcH * 1.01) s = H / (bot0 - top0); // full-height crop: never show the strip
    const winW = W / s;
    const winH = H / s;
    const left = winW >= p.srcW ? (p.srcW - winW) / 2 : Math.min(Math.max(v.cx * p.srcW - winW / 2, 0), p.srcW - winW);
    const top = winH >= p.srcH ? (p.srcH - winH) / 2 : Math.min(Math.max(v.cy * p.srcH - winH / 2, top0), bot0 - winH);
    return { s, left, top, overflow: winH > p.srcH * 1.01 || winW > p.srcW * 1.01 };
  };
  const { s, left, top, overflow } = place(v, life);
  // loop rhyme: the last `loopIn` frames dissolve into frame 0's picture, so the replay starts without a visible jump
  const loopIn = p.loopFrames ?? 9;
  const loopAmt = loopIn > 0 ? ease(clamp01((frame - (p.durationInFrames - loopIn)) / loopIn)) : 0;
  const g0 = place(viewAt(p.beats, 0, fitH, p.segments), 1);
  // motion blur on fast camera moves, so a zoom reads as a camera move and not a jump
  const camSpeed = Math.abs(v.h - vPrev.h) * 40 + Math.hypot(v.cx - vPrev.cx, v.cy - vPrev.cy) * 60;
  const blur = age === 0 ? 0 : Math.min(5, camSpeed * 6); // a cut's first frame is always sharp
  const t = srcTime(p.segments, frame);
  const clears = p.beats.filter((b) => b.do === "clear").map((b) => b.f);
  const endOf = (b: Beat) => b.until ?? clears.find((c) => c > b.f) ?? Infinity;
  const live = p.beats.filter((b) => !["frame", "clear", "captions"].includes(b.do) && frame >= b.f && frame < endOf(b));
  const X = (q: [number, number]) => [q[0] * p.srcW, q[1] * p.srcH] as const;

  const svg: React.ReactNode[] = [];
  const pinned: React.ReactNode[] = [];
  // spotlights go under every other graphic, so they never dim a path or ring drawn earlier
  [...live.filter((b) => b.do === "spot"), ...live.filter((b) => b.do !== "spot")].forEach((b, i) => {
    const k = `${b.do}${i}`;
    const c = b.on ? X(pointAt(b.on, t)) : [0, 0];
    const born = b.f === 0 ? 1 : easeOut(clamp01((frame - b.f) / ((b.dur as number) ?? IN)));
    const alpha = Math.min(b.f === 0 ? 1 : clamp01((frame - b.f) / 3), clamp01((endOf(b) - frame) / OUT));
    if (b.do === "spot") {
      const rx = ((b.r as number) ?? 0.1) * p.srcH;
      svg.push(
        <g key={k} opacity={alpha * born}>
          <defs><mask id={`m${i}`}><rect x={-p.srcW} y={-p.srcH} width={p.srcW * 3} height={p.srcH * 3} fill="white" /><ellipse cx={c[0]} cy={c[1]} rx={rx} ry={rx * ((b.squash as number) ?? 1)} fill="black" style={{ filter: "blur(6px)" }} /></mask></defs>
          <rect x={-p.srcW} y={-p.srcH} width={p.srcW * 3} height={p.srcH * 3} fill="rgba(0,0,0,0.38)" mask={`url(#m${i})`} />
        </g>,
      );
    }
    if (b.do === "ring") {
      const rx = ((b.r as number) ?? 0.09) * p.srcH * (0.88 + 0.12 * born);
      const ry = rx * ((b.squash as number) ?? 0.42);
      const col = (b.color as string) ?? "#fff";
      svg.push(
        <g key={k} opacity={alpha}>
          <ellipse cx={c[0]} cy={c[1]} rx={rx} ry={ry} fill="none" stroke={col} strokeOpacity={0.3} strokeWidth={14} vectorEffect="non-scaling-stroke" pathLength={1} strokeDasharray="1" strokeDashoffset={1 - born} style={{ filter: "blur(3px)" }} />
          <ellipse cx={c[0]} cy={c[1]} rx={rx} ry={ry} fill="none" stroke={col} strokeWidth={5} vectorEffect="non-scaling-stroke" strokeLinecap="round" pathLength={1} strokeDasharray="1" strokeDashoffset={1 - born} />
        </g>,
      );
    }
    if (b.do === "marker") {
      const sz = 30 * (0.6 + 0.4 * born);
      const bob = Math.sin((frame - b.f) / 5) * 4;
      pinned.push(
        <div key={k} style={{ position: "absolute", left: c[0], top: c[1], opacity: alpha, transform: `scale(${1 / s}) translate(-50%, calc(-100% + ${bob}px))`, transformOrigin: "0 0" }}>
          <div style={{ width: 0, height: 0, borderLeft: `${sz * 0.6}px solid transparent`, borderRight: `${sz * 0.6}px solid transparent`, borderTop: `${sz}px solid ${(b.color as string) ?? RED}`, filter: "drop-shadow(0 2px 3px rgba(0,0,0,0.7))", marginBottom: 6 }} />
        </div>,
      );
    }
    if (b.do === "arrow") {
      const a = X(pointAt(b.from, t));
      const z = X(pointAt(b.to, t));
      const bend = ((b.bend as number) ?? 0) * p.srcH;
      const len = Math.hypot(z[0] - a[0], z[1] - a[1]) || 1;
      const m = [(a[0] + z[0]) / 2 - ((z[1] - a[1]) / len) * bend, (a[1] + z[1]) / 2 + ((z[0] - a[0]) / len) * bend];
      const n = 40;
      const grow = b.f === 0 ? 1 : easeOut(clamp01((frame - b.f) / ((b.dur as number) ?? 12)));
      const pts: [number, number][] = [];
      for (let j = 0; j <= Math.max(2, Math.ceil(n * grow)); j++) {
        const u = Math.min(1, j / n);
        pts.push([(1 - u) ** 2 * a[0] + 2 * (1 - u) * u * m[0] + u * u * z[0], (1 - u) ** 2 * a[1] + 2 * (1 - u) * u * m[1] + u * u * z[1]]);
      }
      const [p1, p2] = [pts[pts.length - 2], pts[pts.length - 1]];
      const ang = Math.atan2(p2[1] - p1[1], p2[0] - p1[0]);
      const dashed = !!b.dashed;
      const color = (b.color as string) ?? (dashed ? "#fff" : YELLOW);
      const hd = (dashed ? 20 : 34) / s;
      svg.push(
        <g key={k} opacity={alpha} style={{ filter: "drop-shadow(0 2px 3px rgba(0,0,0,0.6))" }}>
          <polyline points={pts.map((q) => q.join(",")).join(" ")} fill="none" stroke={color} strokeWidth={dashed ? 4.5 : 12} vectorEffect="non-scaling-stroke" strokeLinecap="round" strokeLinejoin="round" strokeDasharray={dashed ? "12 11" : undefined} strokeDashoffset={dashed ? -(frame - b.f) * 0.6 : undefined} />
          {!b.nohead && grow > 0.85 && <polygon points={[[p2[0] + Math.cos(ang) * hd * 0.3, p2[1] + Math.sin(ang) * hd * 0.3], [p2[0] - Math.cos(ang - 0.55) * hd, p2[1] - Math.sin(ang - 0.55) * hd], [p2[0] - Math.cos(ang + 0.55) * hd, p2[1] - Math.sin(ang + 0.55) * hd]].map((q) => q.join(",")).join(" ")} fill={color} strokeLinejoin="round" />}
        </g>,
      );
    }
    if (b.do === "tag" || b.do === "label") {
      const tag = b.do === "tag";
      const col = (b.color as string) ?? (tag ? GREEN : "#fff");
      const rise = (1 - born) * 14;
      const k = ((b.size as number) ?? 34) / 34;
      pinned.push(
        <div key={k} style={{ position: "absolute", left: c[0], top: c[1], opacity: alpha * clamp01(born * 1.5), transform: `scale(${1 / s}) translate(-50%, calc(-100% - ${rise}px))`, transformOrigin: "0 0", display: "flex", flexDirection: "column", alignItems: "center" }}>
          {tag ? (
            <>
              <div style={{ color: "#fff", font: `italic 800 ${(b.size as number) ?? 34}px ${FONT}`, letterSpacing: 0.5, textTransform: "uppercase", whiteSpace: "nowrap", WebkitTextStroke: "1.5px rgba(0,0,0,0.8)", paintOrder: "stroke fill", textShadow: shadow, ...(b.bg ? { background: "rgba(8,10,16,0.62)", padding: "2px 14px 4px", borderRadius: 8 } : {}) }}>{b.text as string}</div>
              <div style={{ width: 0, height: 0, borderLeft: `${11 * k}px solid transparent`, borderRight: `${11 * k}px solid transparent`, borderTop: `${14 * k}px solid ${col}`, marginTop: 3, marginBottom: 8, filter: "drop-shadow(0 2px 2px rgba(0,0,0,0.6))" }} />
            </>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", marginBottom: 14 }}>
              <div style={{ color: col, font: `900 ${(b.size as number) ?? 64}px ${FONT}`, letterSpacing: 1, textTransform: "uppercase", whiteSpace: "nowrap", textShadow: shadow, ...(b.bg ? { background: "rgba(8,10,16,0.62)", padding: "2px 14px 4px", borderRadius: 8 } : {}) }}>{b.text as string}</div>
              <div style={{ height: 5, width: `${100 * born}%`, background: col, borderRadius: 3, marginTop: 2, boxShadow: "0 2px 4px rgba(0,0,0,0.5)" }} />
            </div>
          )}
        </div>,
      );
    }
  });

  return (
    <AbsoluteFill style={{ background: "#000", overflow: "hidden" }}>
      {overflow && seg.bg && (
        <Img src={staticFile(seg.bg)} style={{ position: "absolute", height: H, width: (H * p.srcW) / p.srcH, left: (W - (H * p.srcW) / p.srcH) / 2, filter: "brightness(0.5) blur(4px)" }} />
      )}
      <div style={{ position: "absolute", width: p.srcW, height: p.srcH, transformOrigin: "0 0", transform: `translate(${-left * s}px, ${-top * s}px) scale(${s})`, filter: blur > 0.3 ? `blur(${(blur / s).toFixed(2)}px)` : undefined }}>
        <Img src={staticFile(`${p.frames}/${String(frame + 1).padStart(4, "0")}.jpg`)} style={{ position: "absolute", width: p.srcW, height: p.srcH }} />
        <svg width={p.srcW} height={p.srcH} viewBox={`0 0 ${p.srcW} ${p.srcH}`} style={{ position: "absolute", left: 0, top: 0, overflow: "visible" }}>{svg}</svg>
        {pinned}
      </div>
      {loopAmt > 0 && (
        <div style={{ position: "absolute", width: p.srcW, height: p.srcH, transformOrigin: "0 0", opacity: loopAmt, transform: `translate(${-g0.left * g0.s}px, ${-g0.top * g0.s}px) scale(${g0.s})` }}>
          <Img src={staticFile(`${p.frames}/0001.jpg`)} style={{ position: "absolute", width: p.srcW, height: p.srcH }} />
        </div>
      )}
      {/* house vignette, deeper while frozen so the freeze reads as "pause and look" */}
      <AbsoluteFill style={{ background: "radial-gradient(ellipse at center, transparent 55%, rgba(0,0,0,0.55) 100%)", opacity: 0.45 + 0.45 * freezeAmt(8) }} />
      <ScreenText p={p} frame={frame} />
      <Caption p={p} frame={frame} />
      {p.vo && <Audio src={staticFile(p.vo)} />}
      {p.music && <Audio src={staticFile(p.music)} volume={p.musicVol ?? 1} />}
    </AbsoluteFill>
  );
};

/** One word at a time, white heavy caps with a small pop. `captions` beats move them ({y}) or hide them ({hide: true}). */
/** `text` beats: a screen card (not pinned to the footage) for numbers and results the voice states ("26 OFF 24").
 Dark plate, heavy caps, pops in over 4 frames, fades over the last 5. */
const ScreenText: React.FC<{ p: Props; frame: number }> = ({ p, frame }) => (
  <>
    {p.beats.filter((b) => b.do === "text" && frame >= b.f && frame < (b.until ?? b.f + 45)).map((b, i) => {
      const end = b.until ?? b.f + 45;
      // a hook card at frame 0 is fully on from the first frame (the swipe decision happens there)
      const pop = b.f === 0 ? 1 : interpolate(frame - b.f, [0, 4], [0.85, 1], { extrapolateRight: "clamp" });
      const op = Math.min(b.f === 0 ? 1 : (frame - b.f + 1) / 4, 1, (end - frame) / 5);
      return (
        <div key={i} style={{ position: "absolute", left: 0, right: 0, top: ((b.y as number) ?? 0.2) * H, display: "flex", justifyContent: "center", transform: `translateY(-50%) scale(${pop})`, opacity: op }}>
          <div style={{ color: (b.color as string) ?? "#fff", font: `900 ${(b.size as number) ?? 78}px ${FONT}`, letterSpacing: 1, textTransform: "uppercase", whiteSpace: "nowrap", textShadow: shadow, background: "rgba(8,10,16,0.72)", padding: "6px 26px 10px", borderRadius: 12 }}>{b.text as string}</div>
        </div>
      );
    })}
  </>
);

const Caption: React.FC<{ p: Props; frame: number }> = ({ p, frame }) => {
  const caps = p.captions;
  const i = caps.findIndex((c, k) => frame >= c[0] && (k === caps.length - 1 || frame < caps[k + 1][0]));
  if (i < 0 || frame > caps[i][1] + 12) return null;
  let y = p.capY ?? 0.72;
  let hide = false;
  for (const b of p.beats)
    if (b.do === "captions" && b.f <= frame) {
      y = (b.y as number) ?? y;
      hide = !!b.hide;
    }
  if (hide) return null;
  const pop = interpolate(frame - caps[i][0], [0, 3], [1.18, 1], { extrapolateRight: "clamp" });
  const word = caps[i][2].replace(/[.,;:!?]+$/, "");
  // thick outline + hard drop + dark glow so the word never blends into the pitch or the crowd; numbers in yellow
  return (
    <div style={{ position: "absolute", left: 0, right: 0, top: y * H, transform: `translateY(-50%) scale(${pop})`, textAlign: "center", color: /\d/.test(word) ? "#FFD400" : "#fff", font: `900 88px ${FONT}`, letterSpacing: 1, textTransform: "uppercase", WebkitTextStroke: "11px #000", paintOrder: "stroke fill", textShadow: "0 7px 0 #000, 0 0 22px rgba(0,0,0,0.85), 0 0 44px rgba(0,0,0,0.6)" }}>
      {word}
    </div>
  );
};
