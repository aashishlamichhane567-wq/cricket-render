"""Synthesised sounds for the pointing graphics: ui-ring (circle locks on) and ui-arrow (arrow swish).
Made in code, so there is nothing to license and nothing for Content ID to match. Rerun to regenerate:
  uv run --no-project --with numpy --with soundfile python music/ui_sfx.py
"""
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 24000
OUT = Path(__file__).parent / "sfx"
rng = np.random.default_rng(7)


def band(x, lo, hi):  # crude FFT band-pass, enough for a swish
    f = np.fft.rfft(x)
    hz = np.fft.rfftfreq(len(x), 1 / SR)
    f[(hz < lo) | (hz > hi)] = 0
    return np.fft.irfft(f, len(x))


def ring():
    """A soft tick, then a short tone rising 520 -> 1040 Hz (an octave) that dies away: reads as 'locked on', not as a pop."""
    t = np.arange(int(0.26 * SR)) / SR
    tone = np.sin(2 * np.pi * np.cumsum(520 * 2 ** (np.clip(t / 0.09, 0, 1))) / SR)
    tone += 0.35 * np.sin(2 * np.pi * np.cumsum(1040 * 2 ** (np.clip(t / 0.09, 0, 1))) / SR)  # octave for shine
    env = np.minimum(1, t / 0.006) * np.exp(-t / 0.07)
    click = band(rng.standard_normal(len(t)), 2500, 7000) * np.exp(-t / 0.004)
    return 0.6 * tone * env + 0.25 * click / (np.abs(click).max() + 1e-9)


def arrow():
    """Air moving past: band-passed noise that swells and cuts off, brighter as it goes, about the arrow's draw time (0.4 s)."""
    n = int(0.4 * SR)
    t = np.arange(n) / SR
    lo_, hi_ = band(rng.standard_normal(n), 600, 2500), band(rng.standard_normal(n), 2500, 6500)
    mix = np.clip(t / 0.4, 0, 1)
    env = np.sin(np.pi * np.clip(t / 0.4, 0, 1)) ** 1.6 * np.minimum(1, (0.4 - t) / 0.03)
    return ((1 - mix) * lo_ / np.abs(lo_).max() + mix * hi_ / np.abs(hi_).max()) * env


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, fn in (("ui-ring", ring), ("ui-arrow", arrow)):
        a = fn()
        a = 0.89 * a / np.abs(a).max()  # peak -1 dBFS; the mix sets the real level against the voice
        assert np.isfinite(a).all() and 0.15 < len(a) / SR < 0.5, name
        sf.write(OUT / f"{name}.wav", a.astype(np.float32), SR)
        print(name, f"{len(a) / SR:.2f} s")
