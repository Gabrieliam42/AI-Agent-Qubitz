# Ripple 65B Preview BF16

Select `AI_Agent_Qubitz_Ripple-65B-Preview-BF16.py` under **72 GB VRAM+**.
The launcher uses the three BF16 shards (about 129.6 GB total) at the root of the
[pinned AtomicChat GGUF revision](https://huggingface.co/AtomicChat/Ripple-65B-Preview-GGUF/tree/df550a7178a87bbfb6ee2bdbd37603c553d536ee).
It launches shard `00001-of-00003`.

Like [Q8_0](AI_Agent_Qubitz_Ripple-65B-Preview-Q8_0.md), this launcher requires
the `ricursive` backend support added in b12167. It pins official v1.0.0 (b12359)
and requests 131,072 tokens by default. BF16 needs substantial CPU overflow. The host-memory check does not
count paging as usable RAM or guarantee inference speed.
