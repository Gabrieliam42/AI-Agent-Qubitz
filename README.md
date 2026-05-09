# Qubitz

Qubitz is a local-first standalone AI agent focused on CUDA-accelerated repository assistance, retrieval, and tool use. The current implementation is centered in `AI_Agent_Qubitz.py` and combines a Tk desktop UI, local MCP capabilities, Ollama-based reasoning, GPU embeddings, and FAISS retrieval.

## Current Runtime

- Platform target: WSL2 on Windows 11
- Python: 3.12.3
- GPU target: RTX 24 GB VRAM
- Main model path: `glm-4.7-flash:latest` through Ollama
- Embedding model: `BAAI/bge-code-v1`

## Main Capabilities

- Tk-based local desktop interface
- Local MCP server/client loop
- Windows-Ollama bridge with local HTTP fallback
- CUDA-first code embeddings and FAISS GPU retrieval
- Offline-first embedding workflow with local cache support
- Local skill discovery from `.skills/*/SKILL.md` when present
- Persistent session memory under `.memory/`

## Repository Files

- `.gitignore`: git exclusions for local-only artifacts and runtime data
- `AI_Agent_Qubitz.py`: main standalone agent runtime
- `HARNESS.enc`: encrypted harness in the repository
- `requirements.txt`: runtime dependencies

## Optional Local Runtime Files And Paths

- `RULES.md`: optional local plaintext harness source for maintainers who need to re-encrypt the harness
- `.cache/`: Hugging Face and retrieval cache data
- `.memory/`: current and archived session memory
- `.skills/`: local skill directories
- `.venv/`: WSL Python virtual environment

## Dependency Notes

The active runtime is built around:

- `torch==2.9.1+cu129`
- `transformers==4.57.6`
- `flash-attn==2.8.3`
- `faiss-gpu-cuvs==1.14.1.post1`
- `mcp==1.27.0`

`xformers==0.0.31.post1` is installed in the environment, but its compiled CUDA extensions do not currently load against the active Torch/CUDA stack.

## Status

- Retrieval runs before Ollama generation to preserve GPU headroom for embeddings.
- GPU retrieval resources are released before generation to leave more VRAM for the main model.
- The local MCP server exposes skill-aware resources and tools, including `skills://index`, `list_skills`, `read_skill`, and `read_skill_resource`.
