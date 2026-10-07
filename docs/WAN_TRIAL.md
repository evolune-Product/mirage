# Wan (Alibaba) trial for photo -> talking-person video, on this Mac only

Date: Oct 4-5 2026. Hardware: M1 Pro, 32 GB unified memory, MPS, no NVIDIA. I cannot watch video; judgements below come from stills (a 7-panel strip) and metrics, not from viewing motion.

## 1. Licences (primary sources)

All checked on Hugging Face (`README.md` raw files and the API `cardData`) and the GitHub `LICENSE.txt`.

- Code, `github.com/Wan-Video/Wan2.2` `LICENSE.txt`: stock Apache License 2.0 ("Apache License Version 2.0, January 2004"; the appendix is the unfilled template).
- Weights: the model cards of `Wan-AI/Wan2.2-TI2V-5B`, `Wan-AI/Wan2.2-S2V-14B` and `Wan-AI/Wan2.1-I2V-14B-480P` all carry metadata `license: apache-2.0` and the identical text (Wan2.1-T2V-1.3B shows the same terms):
  > "The models in this repository are licensed under the Apache 2.0 License. We claim no rights over the your generated contents, granting you the freedom to use them while ensuring that your usage complies with the provisions of this license. You are fully accountable for your use of the models, which must not involve sharing any content that violates applicable laws, causes harm to individuals or groups, disseminates personal information intended for harm, spreads misinformation, or targets vulnerable populations."
- The use restrictions are an acceptable-use sentence in the card, not a field-of-use clause in LICENSE.txt. They matter for an avatar product: personal information, impersonation and misinformation are named; consent-based avatars of the user's own face are fine, an upload-any-face product needs a consent flow (already in our design).
- VAE and text encoder: `Wan2.2_VAE.pth` (TI2V-5B) and `Wan2.1_VAE.pth` (S2V) ship inside the same Apache-2.0 repos. The text encoder `models_t5_umt5-xxl-enc-bf16.pth` also ships inside them under the same card; it is Google's umT5-XXL (the card's acknowledgements credit "umt5-xxl"). I did not verify Google's own umT5 licence (google/umt5-xxl is Apache-2.0 on its card per my memory, NOT re-checked here). S2V also bundles `wav2vec2-large-xlsr-53-english` (HF card by jonatasgrosman; licence not verified here, must be checked before shipping S2V).
- Community repackages I used (Comfy-Org/Wan_2.2_ComfyUI_Repackaged: DiT fp16, umt5 fp8-scaled) list `apache-2.0`; QuantStack's Wan2.2-S2V-14B-GGUF also lists apache-2.0 and says "all original licensing terms and usage restrictions remain in effect". Not independently audited beyond the metadata.

Verdict: Wan2.2 TI2V-5B and S2V-14B are commercially usable on paper (Apache-2.0 code and weights), better than the unclear FlashHead-adjacent components. Caveats: the AUP sentence above, and the wav2vec2 / umT5 sub-licences are unverified.

## 2. Wan2.2-TI2V-5B on this Mac

Setup: `workers/wan/Wan2.2` (clone, gitignored), venv `workers/.venv-wan` (python 3.11, torch 2.14.1 MPS), weights in `workers/wan/ckpt` (gitignored, 18 GB downloaded). To stay under the 25 GB cap I did not use the official 34 GB set (fp32 DiT 20 GB + 11 GB T5) but: DiT fp16 10 GB + umT5 fp8-scaled 6.7 GB (both Comfy-Org repack, dequantised on CPU into Wan's key layout) + the official Wan2.2_VAE.pth 2.8 GB. Download is link-limited to about 3.5 MB/s, so it took about 2.5 h.

Patch: `workers/patches/wan-mps.patch` (apply inside the Wan2.2 clone). It: replaces float64/complex RoPE with real cos/sin math; replaces flash-attn with torch SDPA plus a key-padding mask from `k_lens`; swaps every `cuda` device/autocast/empty_cache/synchronize for MPS equivalents; keeps time-embedding/head modules fp32 (the code's fp32 autocast zones) with bf16 weights elsewhere; loads the fp8 umT5; makes S2V/Animate imports optional. Driver `workers/wan/run_ti2v.py` (T5 on CPU, contexts cached on disk, peak-memory sampler), metrics `workers/wan/eval_wan.py` (run with `.venv-face`).

Run: `a man looking at the camera and speaking naturally, subtle head movements, blinking`, input `wa_tight.png`, 480x480, 33 frames (1.4 s at 24 fps), 20 UniPC steps, CFG 5, seed 42, bf16.

| Metric | Result |
|---|---|
| Wall clock, generation call | 933 s (denoise 601 s at ~30 s/step with two forward passes per step; remaining ~330 s is VAE encode + decode in fp32 on MPS) |
| First-use extras | T5 on CPU: ~3.5 min load + ~45 s per prompt (cached afterwards); DiT load ~16 s |
| Memory | process RSS 7-12 GB; MPS driver-allocated peak 45.6 GB (above physical RAM, i.e. allocator cache plus swap/compression pressure; the real working set was not measurable more precisely); `recommended_max_memory` = 26.8 GB. It completed, but the machine is unusable alongside the dev stack during a run. |
| Throughput | ~680 s of compute per second of video. FlashHead on the same machine is ~28 s per video second (docs/MODEL_TRIALS.md), so Wan TI2V is about 24x slower, and ~500x slower than real time. |
| Output | 480x480, 33 frames, clip copied to `~/Desktop/Mirage_wan_trial/wan_ti2v5b_480_33f_20steps.mp4`, strip in `strip.png` |

Metrics (SFace cosine against the source photo, via facelib): face found in 33/33 frames; identity cosine mean 0.748, min 0.630, last frame 0.673 (the same-person threshold for SFace cosine is about 0.363, so it is still recognisably the same face by the metric, but it drifts down over time); jaw-opening range 0.143; head jitter 0.025 (box-centre motion per frame over face width); mean frame-to-frame pixel difference 18.6/255, max 25.7, which is high and consistent with camera/background motion.

What the stills show (frames 0, 6, 13, 19, 26, 32 beside the source): the first half is plausible, with the same man, glasses, shirt and wall, mouth slightly open as if speaking. The wall texture and framing drift (a slow zoom/pan the prompt did not ask for). In the last two panels the face degrades: a red mark appears on the forehead, the moustache and mouth warp, the glasses frames change. Identity preservation therefore weakens within 1.4 s. Longer clips (needed for speech) would need chaining with this drift unmanaged.

Plain statement: this is not an avatar. TI2V-5B takes no audio, so there is no lip sync; the mouth moves because the prompt says "speaking", not because of any speech. It is an image animator. I did not tune steps/resolution further (the time box went mostly to the download); a 704p / 24 fps, 121-frame run would be several times slower still.

## 3. Wan2.2-S2V-14B (the real avatar candidate)

Facts from the model card, README and HF file listings:

- Official guidance: "This command can run on a GPU with at least 80GB VRAM" (single GPU, `--offload_model True --convert_model_dtype`; `--t5_cpu` optional); multi-GPU path is 8 GPUs with FSDP + Ulysses. 480p and 720p supported, default size 1024*704.
- Weights: bf16 DiT 32.6 GB (Comfy repack; the official 4-shard set is about 32.6 GB too), fp8-scaled 16.4 GB, QuantStack GGUF: Q2_K 9.5, Q3_K_M 11.4, Q4_0 12.8, Q4_K_M 13.9, Q5_K_M 15.0, Q6_K 16.2, Q8_0 19.6 GB. Plus umT5-XXL 11.4 GB bf16 (6.7 fp8), Wan2.1 VAE 0.5 GB (fp32; 0.25 GB repack), wav2vec2 0.6 GB.
- Does a quantized variant plausibly fit 32 GB Apple silicon? Weights, yes: Q4 (13 GB) + fp8 umT5 (6.7 GB, can sit on CPU) + VAE fits under the 26.8 GB Metal working-set limit; fp8 (16.4 GB) is borderline and MPS has no native fp8. Activations and time, no: the only quantized path is ComfyUI + ComfyUI-GGUF (dequantises on the fly), which I did not install. The 5B model alone needs ~30 s per step for 2025 tokens; S2V works in 80-frame chunks at 480p (about 19k tokens, attention cost grows quadratically) with a model about 3x larger, so my extrapolation is on the order of tens of minutes per denoise step per chunk, i.e. many hours per few seconds of video. That is an extrapolation, not a measurement.
- Decision: NOT tried. It would have needed ComfyUI (not in the repo), ~14-20 GB more download (cap exceeded: 18 GB already used of 25, at 3.5 MB/s about 1.2 h more) and the time box was spent. I am saying so rather than guessing a result.

Rented GPU needed: official path is 80 GB, so one A100 80GB (about $1.19-1.59/h on RunPod community/secure, per search results: A100 from $1.19/h, A100 SXM $1.39-1.59/h) or H100 80GB (about $1.99-2.89/h PCIe, $2.69-3.49/h SXM) per the RunPod pricing pages found via web search (not re-verified on the vendor page). A 48 GB card (L40S / A6000) could probably run fp8 or Q8 GGUF with offload; 24 GB (4090) only with Q4 plus aggressive offload and slow.

## 4. Verdict

- Wan2.2-TI2V-5B: not worth pursuing for the avatar. No audio input, 24x slower than FlashHead on this Mac, identity drifts inside 1.4 s, camera drift.
- Wan2.2-S2V-14B: the only Wan model that is relevant, and it is a GPU-only (80 GB class) proposition. It cannot be judged from this Mac. On paper it is attractive for cinematic upper-body/expressive output, with clean Apache-2.0 terms. But it is a 14B diffusion model, so even on an H100 expect well below real time (it is a batch/pre-render engine, not a live one). FlashHead stays the engine for what runs on the Mac and for near-real-time.
- Worth one cheap GPU test (a few dollars, owner's decision; I spent nothing and created no accounts). Measure on an 80 GB A100/H100: (a) seconds of compute per output second at 480p and 720p, and peak VRAM, with and without offload; (b) lip sync with the same `clone_b.wav` (12 s) and `wa_tight.png`, using lip-audio correlation, jaw range and SFace identity over time, as in `workers/wan/eval_wan.py` and `/tmp/vocalface_demo/evalface.py`; (c) identity drift across the 80-frame chunk boundaries (the S2V pipeline chains chunks with motion frames); (d) quality of face and teeth at 480p; (e) the fp8 / Q8 variant on a 48 GB card to see if a cheaper GPU is enough; (f) check the wav2vec2 and umT5 licences.
