"""Mirage-1 networks, written from scratch.
Generator: U-Net over [masked target crop | reference crop | mask] (7 ch) with audio injected by cross-attention (8x8 and 16x16
levels) and FiLM (every decoder block). SyncNet: contrastive mouth-crop <-> audio-window embedding (training-time critic).
PatchDisc: small hinge-GAN discriminator on the mouth region."""
import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from . import align

AUD_DIM = 768
WIN = 11  # audio window in video frames (+-5 around the target frame = 0.44 s)


def gn(c):
    return nn.GroupNorm(min(8, c // 4), c)


class Res(nn.Module):
    def __init__(s, cin, cout, film_dim=0):
        super().__init__()
        s.n1, s.c1 = gn(cin), nn.Conv2d(cin, cout, 3, padding=1)
        s.n2, s.c2 = gn(cout), nn.Conv2d(cout, cout, 3, padding=1)
        s.skip = nn.Conv2d(cin, cout, 1) if cin != cout else nn.Identity()
        s.film = nn.Linear(film_dim, 2 * cout) if film_dim else None
        if s.film is not None:
            nn.init.zeros_(s.film.weight), nn.init.zeros_(s.film.bias)

    def forward(s, x, g=None):
        h = s.c1(F.silu(s.n1(x)))
        h = s.n2(h)
        if s.film is not None:
            sc, sh = s.film(g).chunk(2, -1)
            h = h * (1 + sc[..., None, None]) + sh[..., None, None]
        return s.skip(x) + s.c2(F.silu(h))


class CrossAttn(nn.Module):
    """image tokens (queries) attend to audio tokens (keys/values); residual."""
    def __init__(s, c, adim, heads=4):
        super().__init__()
        s.n, s.q = gn(c), nn.Conv2d(c, c, 1)
        s.k, s.v = nn.Linear(adim, c), nn.Linear(adim, c)
        s.o = nn.Conv2d(c, c, 1)
        nn.init.zeros_(s.o.weight), nn.init.zeros_(s.o.bias)
        s.h = heads

    def forward(s, x, a):
        B, C, H, W = x.shape
        q = s.q(s.n(x)).flatten(2).transpose(1, 2)  # B,HW,C
        k, v = s.k(a), s.v(a)
        sp = lambda t: t.reshape(B, -1, s.h, C // s.h).transpose(1, 2)
        o = F.scaled_dot_product_attention(sp(q), sp(k), sp(v)).transpose(1, 2).reshape(B, H * W, C)
        return x + s.o(o.transpose(1, 2).reshape(B, C, H, W))


class AudioEnc(nn.Module):
    def __init__(s, dim=192, layers=2):
        super().__init__()
        s.inp = nn.Sequential(nn.LayerNorm(AUD_DIM), nn.Linear(AUD_DIM, dim))
        s.pos = nn.Parameter(torch.randn(1, WIN, dim) * 0.02)
        s.tf = nn.TransformerEncoder(nn.TransformerEncoderLayer(dim, 4, dim * 2, 0.1, batch_first=True, norm_first=True), layers)
        s.dim = dim

    def forward(s, a):  # a: B,WIN,768
        t = s.tf(s.inp(a) + s.pos)
        return t, t.mean(1)


class LipEnc(nn.Module):
    """Condition = per-frame lip-state vector (jaw/mouth blendshape scores, standardised) -> 4 tokens + pooled vector."""
    def __init__(s, lip_dim, dim=192, ntok=4):
        super().__init__()
        s.net = nn.Sequential(nn.Linear(lip_dim, 256), nn.SiLU(), nn.Linear(256, dim * ntok))
        s.ntok, s.dim = ntok, dim

    def forward(s, z):
        t = s.net(z).reshape(-1, s.ntok, s.dim)
        return t, t.mean(1)


class LipNet(nn.Module):
    """audio window (WIN x 768 wav2vec2 feats) -> lip-state vector. Small, heavily regularised (about 1 minute of training data)."""
    def __init__(s, lip_dim, dim=128):
        super().__init__()
        s.enc = AudioEnc(dim, layers=1)
        s.head = nn.Sequential(nn.Dropout(0.2), nn.Linear(dim, 128), nn.SiLU(), nn.Linear(128, lip_dim))

    def forward(s, a):
        return s.head(s.enc(a)[1])


class Generator(nn.Module):
    def __init__(s, ch=(16, 48, 96, 128, 192), adim=192, lip_dim=0):  # narrow full-res level: MPS speed (measured 2x faster than 32 ch)
        super().__init__()
        s.aud = LipEnc(lip_dim, adim) if lip_dim else AudioEnc(adim)
        s.stem = nn.Conv2d(7, ch[0], 3, padding=1)
        s.enc = nn.ModuleList()
        s.down = nn.ModuleList()
        for i, c in enumerate(ch):
            s.enc.append(Res(c, c))
            if i < len(ch) - 1:
                s.down.append(nn.Conv2d(c, ch[i + 1], 3, stride=2, padding=1))
        s.mid1 = Res(ch[-1], ch[-1], adim)
        s.attn_mid = CrossAttn(ch[-1], adim)
        s.mid2 = Res(ch[-1], ch[-1], adim)
        s.dec, s.up, s.attn_dec = nn.ModuleList(), nn.ModuleList(), nn.ModuleList()
        for i in range(len(ch) - 1, 0, -1):  # 8->16->32->64->128
            s.up.append(nn.Conv2d(ch[i], ch[i - 1], 3, padding=1))
            s.dec.append(Res(ch[i - 1] * 2, ch[i - 1], adim))
            s.attn_dec.append(CrossAttn(ch[i - 1], adim) if i == len(ch) - 1 else nn.Identity())  # attention at 16x16 only
        s.out = nn.Sequential(gn(ch[0]), nn.SiLU(), nn.Conv2d(ch[0], 3, 3, padding=1))

    def forward(s, masked, ref, mask, audio):
        """masked/ref: B,3,S,S in [0,1]; mask: B,1,S,S (1 = hidden region); audio: B,WIN,768. Returns B,3,S,S in [0,1]."""
        tok, g = s.aud(audio)
        h = s.stem(torch.cat([masked, ref, mask], 1))
        skips = []
        for i, blk in enumerate(s.enc):
            h = blk(h)
            skips.append(h)
            if i < len(s.down):
                h = s.down[i](h)
        h = s.mid2(s.attn_mid(s.mid1(h, g), tok), g)
        for up, dec, att in zip(s.up, s.dec, s.attn_dec):
            sk = skips.pop(-2)
            h = up(F.interpolate(h, scale_factor=2, mode="nearest"))
            h = dec(torch.cat([h, sk], 1), g)
            h = att(h, tok) if isinstance(att, CrossAttn) else h
        return torch.sigmoid(s.out(h))


def mouth_slice():
    r0 = int(align.MASK_ROW * align.S) + 2
    return slice(r0, align.S), slice(16, align.S - 16)


class SyncNet(nn.Module):
    """Embeds a mouth-region crop and a 11-frame audio window into one space; trained contrastively on real footage."""
    def __init__(s, d=128):
        super().__init__()
        s.vis = nn.Sequential(nn.Conv2d(3, 32, 3, 2, 1), gn(32), nn.SiLU(), nn.Conv2d(32, 64, 3, 2, 1), gn(64), nn.SiLU(),
                              nn.Conv2d(64, 96, 3, 2, 1), gn(96), nn.SiLU(), nn.Conv2d(96, 128, 3, 2, 1), gn(128), nn.SiLU(),
                              nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Dropout(0.3), nn.Linear(128, d))
        s.aud = nn.Sequential(nn.LayerNorm(AUD_DIM), nn.Dropout(0.3), nn.Linear(AUD_DIM, 192), nn.SiLU())
        s.conv = nn.Sequential(nn.Conv1d(192, 192, 3, padding=1), nn.SiLU(), nn.Conv1d(192, 192, 3, padding=1), nn.SiLU())
        s.proj = nn.Sequential(nn.Dropout(0.3), nn.Linear(192, d))
        s.scale = nn.Parameter(torch.tensor(math.log(10.0)))

    def embed_v(s, img):  # img B,3,S,S
        rs, cs = mouth_slice()
        return F.normalize(s.vis(img[:, :, rs, cs] - 0.5), dim=-1)

    def embed_a(s, a):
        h = s.conv(s.aud(a).transpose(1, 2)).mean(-1)
        return F.normalize(s.proj(h), dim=-1)

    def logits(s, v, a):
        return s.scale.exp() * v @ a.t()


class PatchDisc(nn.Module):
    def __init__(s):
        super().__init__()
        c = lambda i, o, st: [nn.Conv2d(i, o, 4, st, 1), nn.LeakyReLU(0.2)]
        s.net = nn.Sequential(*c(3, 32, 2), *c(32, 64, 2), *c(64, 96, 1), nn.Conv2d(96, 1, 3, 1, 1))

    def forward(s, img):
        rs, cs = mouth_slice()
        return s.net(img[:, :, rs, cs] - 0.5)


def make_mask(B, device):
    m = torch.zeros(B, 1, align.S, align.S, device=device)
    m[:, :, int(align.MASK_ROW * align.S):] = 1
    return m


def count(m):
    return sum(p.numel() for p in m.parameters())
