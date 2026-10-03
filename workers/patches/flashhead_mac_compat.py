"""Run SoulX-FlashHead on Apple silicon: stub the CUDA-only xfuser/flash-attn imports, map cuda syncs to MPS."""
import sys
import types

import torch

if not torch.cuda.is_available():
    for name, attrs in {
        "xfuser": {}, "xfuser.core": {}, "xfuser.core.distributed": {
            "get_sequence_parallel_rank": lambda: 0, "get_sequence_parallel_world_size": lambda: 1, "get_sp_group": lambda: None,
            "init_distributed_environment": lambda *a, **k: None, "initialize_model_parallel": lambda *a, **k: None, "get_world_group": lambda: None},
        "xfuser.core.long_ctx_attention": {"xFuserLongContextAttention": object},
    }.items():
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
    torch.cuda.synchronize = lambda *a, **k: torch.mps.synchronize()
