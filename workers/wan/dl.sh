cd /Users/revanthrajeev/Desktop/Mirage/workers/wan/ckpt
for f in tokenizer.json spiece.model tokenizer_config.json special_tokens_map.json; do curl -sLC - -o google/umt5-xxl/$f https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B/resolve/main/google/umt5-xxl/$f; done
curl -sLC - -o Wan2.2_VAE.pth https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B/resolve/main/Wan2.2_VAE.pth
curl -sLC - -o models_t5_umt5-xxl-enc-bf16.pth https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B/resolve/main/models_t5_umt5-xxl-enc-bf16.pth
curl -sLC - -o dit_fp16.safetensors https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors
echo done > DONE
