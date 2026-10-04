"""Log-mel features at 25 fps, own numpy implementation (no pretrained piece): 40 mel bands, 64 ms FFT window, 40 ms hop."""
import numpy as np


def logmel(wav: np.ndarray, n_mels=40, sr=16000, hop=640, nfft=1024) -> np.ndarray:
    n = max(1, len(wav) // hop)
    w = np.pad(wav, (0, nfft))
    fr = np.stack([w[i * hop:i * hop + nfft] for i in range(n)])
    sp = np.abs(np.fft.rfft(fr * np.hanning(nfft), axis=1)) ** 2
    f = np.linspace(0, sr / 2, nfft // 2 + 1)
    m = lambda x: 2595 * np.log10(1 + x / 700)
    im = lambda x: 700 * (10 ** (x / 2595) - 1)
    pts = im(np.linspace(m(50), m(7500), n_mels + 2))
    fb = np.zeros((n_mels, len(f)))
    for k in range(n_mels):
        a, b, c = pts[k:k + 3]
        fb[k] = np.clip(np.minimum((f - a) / (b - a), (c - f) / (c - b)), 0, None)
    return np.log(sp @ fb.T + 1e-6).astype(np.float32)
