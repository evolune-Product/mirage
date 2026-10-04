# Mirage third-party licence audit

Audit date: 2026-10-03. Scope: every model, weight file and major library that Mirage uses, downloads or has on disk.
**This is an engineering audit, not legal advice. Every row marked UNCLEAR, and every "yes, with conditions" row, needs a lawyer
before you sell or distribute the product.** Licence texts change; re-check the linked source before a release.

How the evidence was gathered (nothing below is from memory alone):
* "on disk" = read from a LICENSE / README / model card / `ollama show --license` output present on this machine.
* "upstream" = the upstream repository or Hugging Face model card page, fetched on the audit date.
* "not verified" = stated from general knowledge or a previous note in `docs/overnight/*.md`; the source could not be read here.

Legend for **Commercial?**: **YES** = permissive licence, no field-of-use restriction. **YES\*** = allowed, with obligations or
use restrictions listed in the next column. **NO** = licence forbids commercial use. **UNCLEAR** = licence is permissive on paper but
training data / provenance / missing text leaves a real doubt. **Status** = what Mirage does with it today.

Quick answer for a commercial build: set `MIRAGE_COMMERCIAL_ONLY=1` (see section 6). That disables Wav2Lip, LivePortrait
(InsightFace weights) and, unless you explicitly opt in, MuseTalk, and leaves the licence-clean viseme engine. The remaining
blockers that the flag does **not** remove are listed in section 5 (eSpeak NG GPL in the Kokoro TTS path, UNCLEAR weights).

---

## 1. Lip-sync, face and image models

| Component | Licence (evidence) | Commercial? | Obligations / notes | Status in Mirage |
|---|---|---|---|---|
| **Wav2Lip** code + `wav2lip.pth`, `wav2lip_gan.pth` (+ `lipsync_expert.pth`, `visual_quality_disc.pth`, S3FD det. in repo) | Non-commercial research only. on disk `workers/Wav2Lip/README.md` L230: "As the models are trained on the LRS2 dataset, any form of commercial use is strictly prohibited." L310: "can only be used for personal/research/non-commercial purposes ... for commercial requests contact rudrabha@synclabs.so" (hosted API sold as Sync Labs). Confirmed upstream. | **NO** | Do not ship, host for customers or demo to paying users. A commercial licence has to be negotiated with the authors. | dev/demo default engine only (`engines/wav2lip.py`, flagged `research_only`). Refused when `MIRAGE_COMMERCIAL_ONLY=1`. |
| **LivePortrait** code (KwaiVGI) | MIT. on disk `workers/LivePortrait/LICENSE` | YES (code) | MIT notice | photo avatar idle clip (`workers/photo_idle.py`) |
| **InsightFace `buffalo_l`** (`det_10g.onnx`, `2d106det.onnx`), downloaded by LivePortrait | on disk `workers/LivePortrait/LICENSE` tail: "The models of InsightFace are for non-commercial research purposes only. If you want to use the LivePortrait project for commercial purposes, you should remove and replace InsightFace's detection models". Upstream insightface README: "The training data containing the annotation (and the models trained with these data) are available for non-commercial research purposes only"; buffalo_l licensing via recognition-oss-pack@insightface.ai. | **NO** | Replace the cropper with MediaPipe (kijai's ComfyUI port does this) or buy an InsightFace licence. | used by photo avatars. `photo_idle.py` / `render_liveportrait.py` exit with "refused" under `MIRAGE_COMMERCIAL_ONLY=1`. |
| **LivePortrait weights** (`liveportrait/base_models/*.pth`, `retargeting_models`, `landmark.onnx`) | HF model card front-matter `license: mit` (on disk `pretrained_weights/README.md`). Training data (public face-video datasets plus an undisclosed part) not stated in what was read. | UNCLEAR | Vendor says MIT; data provenance unknown. needs a lawyer. | used by photo avatars (blocked anyway by InsightFace) |
| LivePortrait animals mode (`liveportrait_animals/*`, `xpose.pth`) and `assets/examples/driving/d0.pkl` (a real person's motion template) | Not verified. | UNCLEAR | Animals mode is not used by Mirage: delete the directory from production images. The `d0.pkl` template is real motion capture of a person: confirm its terms. | animals unused; `d0.pkl` used by `photo_idle.py` |
| **MuseTalk** code (TMElyralab) | MIT, on disk `workers/MuseTalk/LICENSE` ("Copyright (c) 2024 Tencent Music Entertainment Group") | YES | MIT notice | optional engine (`engines/musetalk.py`) |
| **MuseTalk 1.5 `unet.pth`** (`TMElyralab/MuseTalk`) | README L540-541 on disk: "code ... MIT ... no limitation for both academic and commercial usage. `model`: The trained model are available for any purpose, even commercially." Upstream HF card repeats it and says "built on HDTF". README L36: "trained on the HDTF and private dataset". HDTF README (upstream): "available to download under a Creative Commons Attribution 4.0 International License" (the clips themselves are YouTube videos). Test data: "available for non-commercial research purposes only" (not used by Mirage). | UNCLEAR | Vendor-stated commercial OK, but training data = HDTF (YouTube-sourced, CC BY 4.0 as redistributed) + an undisclosed private set. No indemnity. needs a lawyer. Treated as "unclear" by the commercial gate: needs `MIRAGE_COMMERCIAL_ALLOW_UNCLEAR=1`. | optional engine; loaded by `engines/musetalk.py` |
| `sd-vae-ft-mse` (Stability AI) loaded by MuseTalk | upstream HF card: `license: mit`; MuseTalk LICENSE lists it as MIT. | YES | MIT notice | loaded by MuseTalk |
| `whisper-tiny` (openai) loaded by MuseTalk as the audio encoder | MuseTalk LICENSE: MIT (openai/whisper repo). HF `openai/whisper-tiny` card is tagged apache-2.0 (not verified on disk). Either is permissive. | YES | notice | loaded by MuseTalk |
| MuseTalk stack **not loaded by Mirage** (we replaced it with MediaPipe landmarks): DWPose `dw-ll_ucoco_384.pth` | upstream HF card `apache-2.0`; MuseTalk LICENSE lists Apache-2.0. | YES | not loaded | download scripts fetch it; skip |
| ... face-parse-bisent `79999_iter.pth` + `resnet18-5c106cde.pth` | code MIT (face-parsing.PyTorch, upstream); weights trained on **CelebAMask-HQ**, whose own licence was not readable here (commonly cited as non-commercial research). torchvision resnet18 = ImageNet weights. | UNCLEAR | not loaded. Do not enable MuseTalk's own preprocessing (`musetalk/utils/preprocessing`, `blending`) in a commercial build without a lawyer. | not loaded |
| ... S3FD, `latentsync_syncnet.pt` (ByteDance LatentSync, used for MuseTalk training/eval only) | S3FD MIT (MuseTalk LICENSE); LatentSync HF card: OpenRAIL++ (use restrictions). | YES\* / UNCLEAR | not loaded | not loaded |
| **MediaPipe** (code) and `face_landmarker.task` (FaceDetector + FaceMesh-V2 + blendshapes) | pip metadata on disk: Apache-2.0 (mediapipe 0.10.21). Google's Face Landmarker page: code samples Apache-2.0, content CC BY 4.0; the page points to model cards for the weights' terms and does not itself state them. | YES (verify model card) | keep NOTICE; read the 3 linked model cards once before launch | face tracking in every engine; the **only** model in the licence-clean path |
| **OpenCV** (headless 4.11) | pip metadata: Apache-2.0 | YES | notice | all engines |
| OpenCV Haar cascade (`haarcascade_frontalface_default.xml`) | Intel/BSD-style licence header inside the file, not verified here | YES (verify) | notice | fallback detector when mediapipe is missing |
| **Real-ESRGAN** `realesr-general-x4v3.pth` | code BSD-3-Clause (upstream). The repo states no separate licence for the weights; "trained with pure synthetic data" from degraded public datasets. | UNCLEAR | needs a lawyer | optional offline `--restore sr` only |
| **GFPGAN** 1.4 | code Apache-2.0 (upstream). Weights trained on **FFHQ** (dataset licence CC BY-NC-SA 2.0 for the dataset; per-image licences vary: not verified here). | UNCLEAR | rejected for quality; not shipped | optional offline only, not default |
| `openai/clip-vit-base-patch32`, `sentence-transformers/all-MiniLM-L6-v2` (apache-2.0 per its card on disk) | present only in the HF cache from earlier experiments; no code path in Mirage loads them (grep) | n/a | delete from images | unused |

## 2. Speech models

| Component | Licence (evidence) | Commercial? | Obligations / notes | Status |
|---|---|---|---|---|
| **faster-whisper** (code) | MIT, pip metadata on disk | YES | notice | STT default |
| Whisper weights via `Systran/faster-whisper-base`, `-small` | HF cards on disk: `license: mit`. (`tiny.en`, `base.en`, `small.en`, `distil-small.en` also in cache: cards not on disk; upstream Whisper is MIT, distil-whisper MIT, not verified) | YES | notice | default `base`; others optional |
| CTranslate2 | MIT, pip metadata on disk | YES | notice | runtime |
| **Kokoro-82M** weights, `kokoro-v1.0.onnx`, `voices-v1.0.bin` | HF card upstream `apache-2.0`; kokoro-onnx README: model files Apache-2.0. Card says it was trained on public-domain / permissively licensed audio plus synthetic audio from closed commercial TTS ("developers emphasize ... commercial viability"). CC-BY attribution for small Koniwa (CC BY 3.0) and SIWIS (CC BY 4.0) subsets. | YES\* | Apache notice; credit the CC BY datasets (Koniwa, SIWIS) if you redistribute the weights | TTS default |
| `kokoro-onnx` (code) | MIT (upstream README) | YES | notice | TTS runtime |
| **eSpeak NG** (bundled as `espeakng_loader/libespeak-ng.dylib` + `espeak-ng-data`) and **`phonemizer-fork` 3.3.1** | eSpeak NG upstream: "released under the GPL version 3 or later license". `phonemizer-fork` pip metadata on disk: GPLv3+. kokoro-onnx hard-depends on both and its README warns about the GPL dependency. The wheels carry no licence text. | **UNCLEAR / likely NO if you distribute binaries** | GPL-3.0 is copyleft. Running it inside your own hosted service is generally *not* distribution (it is not AGPL), but shipping a Docker image, on-prem build or desktop client that contains it can trigger the duty to offer corresponding source for the GPL parts and possibly the linked work. **needs a lawyer.** Mitigations: SaaS-only hosting; run phonemization as a separate process; or replace the phonemizer with a permissive G2P and re-validate Kokoro output. | TTS default (blocker for distribution, not flagged by the flag) |
| **Chatterbox** (Resemble AI) weights / `mlx-community/chatterbox-4bit` | HF card upstream: `license: MIT`; built-in PerTh watermark in output; backbone is a Llama-3 architecture. Prior note (`voice-cloning.md`): the mlx-community conversion card lists Apache-2.0. | YES\* | MIT notice; keep the watermark (it is part of the model; removing it would be your own risk); the vendor says MIT despite the Llama-architecture backbone: needs a lawyer if you want certainty that no Llama licence terms attach | voice cloning (optional venv `.venv-clone`) |
| `mlx-community/S3TokenizerV2`, `mlx-audio` | MIT per `voice-cloning.md`; not verified on disk | YES (not verified) | | with Chatterbox |
| **WeSpeaker** `wespeaker_resnet34_lm.onnx` (consent voice match) | toolkit Apache-2.0 (upstream); HF card for `Wespeaker/wespeaker-voxceleb-resnet34-LM`: `CC-BY-4.0`, "trained on VoxCeleb2 Dev". | UNCLEAR | Attribution (CC BY). VoxCeleb is built from YouTube videos; terms of using models trained on it commercially are not settled in what was read. needs a lawyer | consent verification (default) |
| **Silero VAD** `silero_vad.onnx` | MIT (upstream: "Published under permissive license (MIT) ... no telemetry, no keys, no registration") | YES | notice | VAD default |
| `torch`, `torchvision` | BSD-style, pip metadata on disk | YES | notice | workers |
| `diffusers`, `transformers` | Apache-2.0, pip metadata on disk | YES | notice | MuseTalk only |
| `librosa` (ISC), `scipy` (BSD), `numpy` (BSD/0BSD/MIT/Zlib/CC0), `onnxruntime` (MIT), `soundfile` (BSD), `av` (BSD-3-Clause) | pip metadata on disk. Note `av` wheels bundle FFmpeg libraries: check which FFmpeg build (LGPL vs GPL) the wheel carries before shipping images. | YES | notices | runtime |
| `soxr` (python-soxr, pulled by librosa in `.venv-face`) | LGPL-2.1-or-later (pip metadata on disk) | YES\* (dynamic link) | LGPL: allow relinking / replacement; fine for hosted use, mention in notices | workers |

## 3. Retrieval and language models (Ollama)

| Component | Licence (evidence) | Commercial? | Obligations / notes | Status |
|---|---|---|---|---|
| **Ollama** (runtime) | MIT (not verified here) | YES | | LLM host |
| **Llama 3.2 1B / 3B** (`llama3.2:1b`, `llama3.2:3b`) | Llama 3.2 Community License Agreement, text read via `ollama show --license` | YES\* | "Built with Llama" must be prominently displayed (L44); keep a copy of the agreement when you distribute; "Llama" at the start of the name of any model you train with its outputs; if your products exceed 700 million monthly active users you must request a licence (L65); governed by California law; Acceptable Use Policy applies (L116 "Prohibited Uses"). Not a field-of-use ban for ordinary SaaS. | `llama3.2:1b` voice default, `llama3.2:3b` knowledge default + eval judge |
| **Qwen3** 4B / 8B (`qwen3:4b`, `qwen3:8b`) | Apache-2.0, text on disk via `ollama show --license` | YES | notice | moderation + tool personas |
| **Gemma 3 4B** (`gemma3:4b`) | Gemma Terms of Use (read on disk). | YES\* | Hosted services are allowed. You must (i) flow the Gemma use restrictions down to your users, (ii) include the notice "Gemma is provided under and subject to the Gemma Terms of Use found at ai.google.dev/gemma/terms" with any non-hosted distribution, (iii) respect the Prohibited Use Policy, (iv) accept that Google may restrict usage it believes violates the terms and that you should "make reasonable efforts to use the latest version". | perception (VLM) default |
| **Mistral Small 3.2 24B** (`mistral-small3.2:24b`) | Apache-2.0, text on disk | YES | notice | optional large model |
| **moondream** (`moondream:latest`) | Apache-2.0, text on disk | YES | notice | optional light VLM |
| `nova-companion:latest` | a custom model from another project on this Mac | n/a | not part of Mirage | not used |
| qwen2.5vl:3b | Qwen Research licence (non-commercial) per `intelligence.md` | NO | rejected, never used | not used |
| `fastembed` (code) | Apache-2.0, pip metadata + NOTICE on disk | YES | keep NOTICE | knowledge base |
| **BAAI/bge-small-en-v1.5** | upstream HF card: MIT, "can be used for commercial purposes free of charge" | YES | notice | embeddings default (auto-downloaded on first use) |

## 4. Application libraries, fonts, assets

| Component | Licence (evidence) | Commercial? | Notes |
|---|---|---|---|
| Backend: FastAPI, Starlette, Pydantic, SQLModel, httpx, Alembic, uvicorn, websockets | MIT / BSD (pip metadata, scan of the ~90 packages in `backend/.venv`: all permissive except the rows below) | YES | notices |
| `certifi`, `tqdm` (MPL-2.0) | file-level copyleft, unmodified use | YES | notice |
| `cryptography` (Apache-2.0 OR BSD-3), `pillow` (MIT-CMU), `protobuf` (BSD-3) | pip metadata | YES | notices |
| `phonemizer-fork` | **GPLv3+** (see eSpeak NG row) | see above | the only GPL Python package in the backend env |
| Web: Next.js, React, three.js, framer-motion, Tailwind (MIT), lucide-react (ISC) | `package.json` in `web/node_modules` on disk | YES | notices |
| Web fonts: Instrument Serif, Inter, JetBrains Mono via `next/font/google` (self-hosted at build) | SIL Open Font License 1.1 (not verified here) | YES | keep OFL notices; do not sell the fonts on their own |
| Caption renderer fonts (`workers/captions.py` / `creative_render.py`) | tries macOS system Arial Bold, then DejaVu Sans Bold. Arial is Apple/Monotype-licensed: **not redistributable**; DejaVu is free (Bitstream Vera licence) | YES (DejaVu only) | production images must contain DejaVu/Liberation and no Arial (Docker already uses the Linux path) |
| FFmpeg (system binary, invoked as a subprocess) | local build is `--enable-gpl` | YES\* | not linked into Mirage; shipping an image that contains a GPL build obliges you to offer its source. Prefer an LGPL build in images |
| Demo/test footage (`SpendVeto_Founder_Video_1min.mp4` etc.) | owner's own video | n/a | do not ship customer-visible demos of other people without consent |

## 5. What is still a blocker or open question after `MIRAGE_COMMERCIAL_ONLY=1`

1. **eSpeak NG / phonemizer-fork (GPL-3.0+) inside the Kokoro TTS path.** Affects any *distributed* artefact (Docker image, on-prem install). Hosted-only is the common safe harbour, but ask a lawyer.
2. **WeSpeaker (VoxCeleb2-trained)**, **Chatterbox (Llama-architecture backbone)**, **MuseTalk unet (HDTF + private data)**, **LivePortrait weights**, **Real-ESRGAN / GFPGAN weights**: licences are permissive but training-data provenance is not clean enough to call "yes".
3. **Attribution screens:** a "Built with Llama" notice (Llama 3.2), a Gemma notice, CC BY credits (WeSpeaker, Kokoro datasets) and a NOTICE bundle for MIT/Apache components. Not built yet: add an `/about/licences` page and put the notices in the Docker images.
4. **Photo avatars** still depend on InsightFace through LivePortrait: the flag blocks the feature; the replacement (MediaPipe cropper in LivePortrait, or another idle-clip generator) is not built.
5. **Likeness and consent law** (voice/face cloning, EU AI Act transparency for synthetic media, deepfake statutes) is outside this audit.

## 6. How Mirage enforces this

* `MIRAGE_COMMERCIAL_ONLY=1`: the lip-sync service (`workers/lipsync_server.py`) refuses to load any engine whose licence metadata is `commercial=False` (Wav2Lip) and, by default, `commercial=None` (MuseTalk). `/health` reports `commercial_only`, `engine`, `engine_licence`, `disabled_engines`, per-engine status. `render_offline.py`, `photo_idle.py` and `render_liveportrait.py` exit with a "refused: ..." message.
* `MIRAGE_COMMERCIAL_ALLOW_UNCLEAR=1`: operator opt-in, after legal review, that unlocks UNCLEAR-but-vendor-permissive engines (MuseTalk). It never unlocks research-only engines.
* Licence metadata lives next to the code: `workers/engines/*.py` (`LicenceInfo`, `Weight`), tests in `backend/tests/test_engines.py`.
* `MIRAGE_LIPSYNC_ENGINE=auto|viseme|musetalk|wav2lip` selects the engine; default `auto` = Wav2Lip in dev (unchanged behaviour), MuseTalk (if allowed and loadable) else viseme under the flag.

## SoulX-FlashHead (candidate generative face model)
- Soul-AILab/SoulX-FlashHead, 1.3B diffusion transformer, audio-driven whole-face + head motion, real-time streaming on NVIDIA.
- README states Apache-2.0 for code and weights (unverified by a lawyer; check each bundled VAE/wav2vec2 licence before commercial use).
- Mac (MPS) port: `workers/patches/flashhead-mps.patch` + `flashhead_mac_compat.py`; run in `workers/.venv-flash` (transformers 4.57.3).
- Measured on M1 Pro, Model_Lite: ~19x slower than real time (6 s clip = 113 s). Real-time needs an NVIDIA GPU (unverified here).

**Decision (Oct 4):** SoulX-FlashHead is the only generative face model we keep. JoyVASA was tried and dropped (poor quality, depended on LivePortrait/InsightFace). Wav2Lip remains only as the dev-mode live engine until FlashHead runs real time on a GPU.
