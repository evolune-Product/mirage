# Benchmarks (M1 Pro, 34 GB, Oct 2 2026)

| Stage | Result |
|---|---|
| faster-whisper base.en (CPU int8) | 0.3 s / utterance |
| Ollama llama3.2:1b, warm | 0.1-0.2 s first token |
| Kokoro TTS (CPU) | ~1.2 s / short sentence |
| Voice loop, time to first audio | 1.5-1.9 s |
| LivePortrait on MPS, 512px, 78 frames | ~170 s => ~0.5 fps (needs 25) |

Conclusion: voice side is solved locally for free. Real-time face rendering is NOT feasible
on Apple Silicon with LivePortrait (grid_sample 3D falls back to CPU). Needs an NVIDIA GPU
(LivePortrait reports ~12 ms/frame on RTX 4090) or a lighter renderer.

## Face engines on the same 18 s script (Oct 4, M1 Pro, objective proxies)
Same text and audio; MediaPipe jawOpen vs audio loudness (best lag, Pearson), SFace identity cosine vs the source photo, mean Laplacian variance of the face crop.

| Engine | Licence | lip-audio corr | identity cos | jaw range | sharpness | render speed |
|---|---|---|---|---|---|---|
| Real footage (calibration) | – | 0.14 | 0.84 | 0.36 | 37.6 | – |
| Wav2Lip | non-commercial | 0.15 | 0.87 | 0.23 | 48.4 | real time |
| MuseTalk | unclear | 0.29 | 0.85 | 0.20 | 48.7 | 3 fps |
| JoyVASA | non-commercial (via LivePortrait) | 0.04 | 0.92 | 0.08 | 57.6 | 87x slower than real time |
| **FlashHead Lite** | Apache-2.0 (README) | 0.17 | 0.91 | 0.41 | 24.5 | 19x slower than real time |

Caveats: the lip-audio correlation is a weak proxy (real footage scores only 0.14), so it cannot rank engines; it only shows JoyVASA barely moves its mouth. Sharpness depends on output resolution and upscaling (FlashHead renders 512x512 and is softer). Paid products (Tavus, HeyGen, D-ID) were NOT tested: no accounts. No human blind test was run; the owner judged FlashHead the best by eye.
