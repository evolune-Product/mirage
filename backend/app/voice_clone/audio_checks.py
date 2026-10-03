"""Programmatic sanity checks for synthesized speech (we cannot listen): clipping, silence, DC offset, NaNs,
discontinuity clicks, over-long internal pauses, and a flatness/bandwidth hint for robotic or broken output.
Pure numpy; input is mono float32 in [-1, 1]. `check()` returns metrics plus a list of human-readable `problems`."""
import numpy as np


def check(x: np.ndarray, sr: int = 24000) -> dict:
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    out: dict = {"seconds": round(len(x) / sr, 3), "problems": []}
    p = out["problems"]
    if len(x) < sr * 0.3:
        p.append("shorter than 0.3 s")
        return out
    if not np.isfinite(x).all():
        p.append("NaN/inf samples")
        x = np.nan_to_num(x)
    peak = float(np.abs(x).max())
    out["peak"] = round(peak, 4)
    out["rms_dbfs"] = round(20 * np.log10(float(np.sqrt(np.mean(x ** 2))) + 1e-9), 1)
    # clipping: samples at (nearly) full scale, or runs of >= 3 identical extreme samples
    out["clipped_fraction"] = round(float(np.mean(np.abs(x) >= 0.999)), 6)
    if out["clipped_fraction"] > 0.0005:
        p.append(f"clipping ({out['clipped_fraction']:.4%} of samples at full scale)")
    if peak < 0.05:
        p.append("very quiet (peak < 0.05)")
    out["dc_offset"] = round(float(np.mean(x)), 5)
    if abs(out["dc_offset"]) > 0.01:
        p.append("DC offset")
    # frame energy (20 ms) -> leading/trailing/internal silence
    fl = int(0.02 * sr)
    n = len(x) // fl
    e = 20 * np.log10(np.sqrt(np.mean(x[: n * fl].reshape(n, fl) ** 2, axis=1)) + 1e-9)
    voiced = e > max(e.max() - 40, -70)
    out["silence_fraction"] = round(float(1 - voiced.mean()), 3)
    if not voiced.any() or voiced.mean() < 0.3:
        p.append(f"mostly silence ({out['silence_fraction']:.0%})")
    idx = np.flatnonzero(voiced)
    if len(idx):
        out["leading_silence_s"] = round(idx[0] * 0.02, 2)
        out["trailing_silence_s"] = round((n - 1 - idx[-1]) * 0.02, 2)
        gaps = np.diff(idx) - 1
        longest = float(gaps.max() * 0.02) if len(gaps) else 0.0
        out["longest_internal_pause_s"] = round(longest, 2)
        if out["leading_silence_s"] > 0.8 or out["trailing_silence_s"] > 0.8:
            p.append("long leading/trailing silence")
        if longest > 1.5:
            p.append(f"internal pause of {longest:.1f} s")
    # clicks: sample-to-sample jumps far above what speech does at this level
    d = np.abs(np.diff(x))
    thr = max(0.35, 12 * float(np.percentile(d, 99.5)))
    out["click_count"] = int((d > thr).sum())
    if out["click_count"] > 0:
        p.append(f"{out['click_count']} discontinuity click(s)")
    # spectrum: bandwidth (95% energy rolloff) and flatness over voiced frames
    spec = np.abs(np.fft.rfft(x[: n * fl].reshape(n, fl)[voiced] * np.hanning(fl), axis=1)) ** 2 if voiced.any() else None
    if spec is not None:
        mean = spec.mean(axis=0) + 1e-12
        cum = np.cumsum(mean) / mean.sum()
        out["rolloff95_hz"] = int(np.searchsorted(cum, 0.95) * sr / fl)
        out["spectral_flatness"] = round(float(np.exp(np.mean(np.log(mean))) / np.mean(mean)), 5)
        if out["rolloff95_hz"] < 1500:
            p.append("very narrow bandwidth (muffled)")
        if out["spectral_flatness"] > 0.2:
            p.append("noise-like spectrum")
    out["ok"] = not p
    return out
