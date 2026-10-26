# 48 GB VRAM+ Tier

The ten public large-tier variants target two 24GB GPUs plus available system RAM. On the developer machine this is two RTX 3090s and 128GB installed RAM. Other systems do not need exactly 128GB RAM; capacity depends on the selected model, context, runtime overhead and competing workloads. The tier is not a 48GB model-size ceiling.

Launchers select `CUDA0,CUDA1`, use layer splitting, inspect current free VRAM and retain a 1536 MiB margin per GPU. GPU-first fitting leaves weight overflow in CPU-addressable memory rather than rejecting a model simply because it exceeds VRAM. The two devices remain separate allocations, not one unified 48GB buffer. No fixed 128GB RAM allocation or capacity cap is set.

The variants retain their existing llama.cpp runtimes. Automatic fitting uses `--fit on` and per-device `--fit-target`, with a minimum fit context of 131072 except GPT-OSS-120B's existing 32768. Variants with explicit layer placement retain that placement. Context, sampling and model choices remain variant-specific; a model's native context ceiling does not guarantee a usable memory fit.

Available RAM must be sufficient for offloaded weights, KV state, compute buffers and the application. Under WSL, the guest memory limit may be below installed Windows RAM. Memory mapping is not extra physical RAM; paging can cause severe slowdowns. CPU offload can also be substantially slower than GPU-resident execution. No throughput or large-context feasibility claim follows from model-free launch checks.

Automatic-fit launchers strip inherited GPU-layer and tensor-split overrides so the selected runtime can place weights using its own fitting policy. Variants with explicit layer placement keep that model-specific policy. Runtime versions and supported flags are pinned per launcher; for example, [Ornith-1.5-122B](AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.md) and [Nemotron-3.5-Super-VL](AI_Agent_Qubitz_Nemotron-3.5-Super-VL-120B-A12B-UD-Q6_K_XL.md) use isolated runtime releases. Consult each model guide for its runtime details rather than assuming one backend revision applies to the whole tier.
