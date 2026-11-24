# Ripple 65B Preview Q8_0

Select `AI_Agent_Qubitz_Ripple-65B-Preview-Q8_0.py` under **72 GB VRAM+**.
It uses two root-level Q8_0 shards (about 68.8 GB total) from
`AtomicChat/Ripple-65B-Preview-GGUF` at revision
`df550a7178a87bbfb6ee2bdbd37603c553d536ee`.

The launcher requests 131,072 context tokens by default, uses an F16 KV cache,
and pins llama.cpp v1.0.0 (b12359). Layer fitting can place overflow in system
RAM. Reduce the requested context if memory admission fails.
