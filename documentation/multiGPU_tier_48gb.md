# 48 GB VRAM+ Tier

The ten public 48 GB VRAM+ variants target aggregate GPU capacity plus available system RAM. They now use all compatible GPUs visible to the selected backend, including three, two or one, rather than requiring exactly two cards. Other systems do not need exactly 128GB RAM; capacity depends on the selected model, context, runtime overhead and competing workloads. The tier is guidance, not a fixed GPU count or model-size ceiling.

Launchers select backend-visible device IDs, use layer splitting, inspect current free VRAM and retain a 1536 MiB margin per GPU. Explicit device restrictions are respected; busy GPUs that cannot retain their margin cause a clear error rather than an unsafe allocation. GPU-first fitting leaves weight overflow in CPU-addressable memory rather than rejecting a model simply because it exceeds VRAM. Devices remain separate allocations, not one unified buffer. No fixed 128GB RAM allocation or capacity cap is set. GPU brands must be supported by the selected backend; using more cards is not a throughput guarantee.

The variants retain their existing llama.cpp runtimes. Automatic fitting uses `--fit on` and per-device `--fit-target`, with a minimum fit context of 131072 except GPT-OSS-120B's existing 32768. Variants with explicit layer placement retain that placement. Context, sampling and model choices remain variant-specific; a model's native context ceiling does not guarantee a usable memory fit.

Available RAM must be sufficient for offloaded weights, KV state, compute buffers and the application. Under WSL, the guest memory limit may be below installed Windows RAM. Memory mapping is not extra physical RAM; paging can cause severe slowdowns. CPU offload can also be substantially slower than GPU-resident execution. No throughput or large-context feasibility claim follows from model-free launch checks.

The [GLM-5.3-Flash IQ4 variant](AI_Agent_Qubitz_GLM-5.3-Flash-UD-IQ4_XS.md)
has a separate 72 GB VRAM+ tier and likewise selects all visible compatible GPUs. Its isolated ik_llama backend uses
flag-only `--fit` with `--fit-margin`. It retains 202752 default context and
Windows/WSL memory guards. Online startup prepares its missing pinned backend;
historical short-test passes did not establish long-run stability.

The Qwen3.8 Flash Next [UD-Q4_K_XL](AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-Q4_K_XL.md)
profile also uses the 72 GB VRAM+ label. The [variant catalog](qubitz_variants_list.md)
lists the other available large-model profiles.

Automatic-fit launchers strip inherited GPU-layer and tensor-split overrides so the selected runtime can place weights using its own fitting policy. Variants with explicit layer placement keep that model-specific policy. Runtime versions and supported flags are pinned per launcher; for example, [Ornith-1.5-122B](AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.md) and [Nemotron-3.5-Super-VL](AI_Agent_Qubitz_Nemotron-3.5-Super-VL-120B-A12B-UD-Q6_K_XL.md) use isolated runtime releases. Consult each model guide for its runtime details rather than assuming one backend revision applies to the whole tier.
