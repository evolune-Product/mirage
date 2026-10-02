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
