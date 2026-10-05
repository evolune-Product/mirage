"""Minimal LoRA (own implementation, no peft) for SoulX-FlashHead's WanModelAudioProject. Base weights stay frozen/bf16, adapters fp32."""
import torch, torch.nn as nn

class LoRALinear(nn.Module):
    def __init__(self, base: nn.Linear, r=8, alpha=8):
        super().__init__()
        self.base, self.r, self.scale, self.enabled = base, r, alpha / r, True
        self.A = nn.Parameter(torch.randn(r, base.in_features, device=base.weight.device) / base.in_features ** 0.5)
        self.B = nn.Parameter(torch.zeros(base.out_features, r, device=base.weight.device))  # zero-init: starts == base
        self.mult = 1.0
    def forward(self, x):
        y = self.base(x)
        if not self.enabled or self.mult == 0: return y
        return y + ((x.float() @ self.A.t()) @ self.B.t() * (self.scale * self.mult)).to(y.dtype)

# attention (self + audio cross) projections in every block, and the audio projection MLP
TARGETS = ("self_attn.q", "self_attn.k", "self_attn.v", "self_attn.o", "cross_attn.q", "cross_attn.k", "cross_attn.v", "cross_attn.o",
           "audio_proj.proj1", "audio_proj.proj1_vf", "audio_proj.proj2", "audio_proj.proj3")

def inject(model, r=8, alpha=8, targets=TARGETS):
    names = [n for n, m in model.named_modules() if isinstance(m, nn.Linear) and any(n.endswith(t) for t in targets)]
    for n in names:
        parent = model.get_submodule(n.rsplit(".", 1)[0]); leaf = n.rsplit(".", 1)[1]
        setattr(parent, leaf, LoRALinear(getattr(parent, leaf), r, alpha))
    return names

def params(model): return [p for n, p in model.named_parameters() if n.endswith(".A") or n.endswith(".B")]
def state(model): return {n: p.detach().cpu() for n, p in model.named_parameters() if n.endswith(".A") or n.endswith(".B")}
def load(model, sd):
    own = dict(model.named_parameters())
    for n, v in sd.items(): own[n].data.copy_(v.to(own[n].device))
def set_mult(model, m):
    for mod in model.modules():
        if isinstance(mod, LoRALinear): mod.mult = m
