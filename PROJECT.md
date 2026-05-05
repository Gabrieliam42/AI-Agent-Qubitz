# AI_Agent_Qubitz

## Environment
- Workspace root: `D:\AI_Research\AI_Agent_Qubitz`
- Active runtime: WSL2 project venv at `.venv`
- Python: `3.12.3`
- Platform target: WSL2 on Windows 11, RTX 3090 24 GB VRAM

## Core Files
- `AI_Agent_Qubitz.py`: main standalone agent. Provides the Tk GUI, local MCP server/client loop, Windows-Ollama bridge with fallback, CUDA-first BGE embedding, FAISS GPU retrieval, local skill discovery/activation, and persistent memory handling.
- `HARNESS.txt`: governing local agent harness. Defines behavior, safety, tool-use, workflow, and output rules for the project.
- `RULES.md`: project-level operating instructions loaded after `HARNESS.txt`. Defines project-specific rules, stack expectations, and workflow constraints for the runtime.
- `AI_Agent.txt`: architectural notes and source references for the intended local agent setup.
- `requirements.txt`: runtime dependency manifest for the local agent.
- `requirements-ci.txt`: CI/lint/test-only Python packages.
- `changelog.txt`: backup/change log for protected `.py` edits.

## Agent Behavior
- The agent is local-first and standalone. It uses `glm-4.7-flash:latest` through Ollama for reasoning, tool use, editing, and final answers.
- `HARNESS.txt` is read first and injected into the system prompt; `RULES.md` is then read and injected immediately after it as project rules.
- The default runtime settings are `num_ctx=16384`, `num_predict=4096`, and `max_steps=16` for the agent loop.
- Explicit file-read requests are handled deterministically by direct file reads before the model loop.
- Semantic repository retrieval uses `BAAI/bge-code-v1` embeddings on CUDA when available, with `faiss-gpu-cuvs` as the primary vector-search backend and CPU fallback paths where needed.
- The embedder path is offline-by-default. Local Hugging Face cache files are required unless `QUBITZ_ALLOW_EMBED_ONLINE=1` is set explicitly.
- Retrieval runs before loading the Ollama generation model so the embedder gets GPU headroom first.
- Retrieval GPU resources are released before Ollama generation so more VRAM stays available for `glm-4.7-flash`.
- Local skills are discovered from `.skills/*/SKILL.md`, parsed into a runtime registry, and activated when the prompt matches their metadata.
- The GUI is Tk-based with a dark anthracite theme and white text.
- The GUI startup transcript shows the workspace, main model, embedding model, runtime defaults, and local skill count.
- The runtime status stream reports loaded local skill count at the beginning of each request.
- Session memory is stored in `.memory/MEMORY.md` and archived to `.memory/MEMORY_<timestamp>.md`.

## Dependencies
- Installed runtime packages:
  - `torch==2.9.1+cu129`
  - `torchvision==0.24.1+cu129`
  - `torchaudio==2.9.1+cu129`
  - `transformers==4.57.6`
  - `flash-attn==2.8.3`
  - `xformers==0.0.31.post1` (installed but its compiled CUDA extensions do not load against the current Torch/CUDA stack)
  - `faiss-gpu-cuvs==1.14.1.post1`
  - `mcp==1.27.0`
  - `httpx==0.28.1`
  - `accelerate==1.13.0`
  - `numpy==2.4.4`
  - `sentencepiece==0.2.1`
  - `einops==0.8.2`

## Requirements Status
- `requirements.txt` now targets the active CUDA 12.9 / Python 3.12 runtime and includes the external libraries used by the agent runtime.
- `flash-attn` is the active attention-acceleration path and is pinned to the official prebuilt wheel for `cp312 + torch 2.9 + cu12`.
- `xformers` is intentionally excluded from `requirements.txt` on the current `torch 2.9.1+cu129` stack because a stable official matching `cu129` wheel is not currently selected for this project.
- `requirements-ci.txt` is kept separate for `pytest`, `pytest-cov`, and `ruff`.

## Structure
- `.cache/`: Hugging Face and retrieval cache data.
- `.memory/`: current and archived session memory files.
- `.venv/`: WSL Python 3.12 virtual environment used by the project.
- `.skills/`: local skill directories using Agent Skills-style `SKILL.md` files plus optional `scripts/`, `references/`, and `assets/`.

## Solutions
- The current agent implementation is concentrated in `AI_Agent_Qubitz.py` rather than split into multiple modules.
- CUDA-first embedding and FAISS GPU retrieval are working in the current environment.
- FlashAttention 2 is the preferred attention optimization path for the embedder on the active runtime.
- Ollama access is designed to prefer the Windows-side install from WSL while falling back to direct local HTTP when needed.
- The local MCP server now exposes skill-aware capabilities including a `skills://index` resource and `list_skills`, `read_skill`, and `read_skill_resource` tools.

## Failures
- `xformers==0.0.31.post1` remains installed in the environment but its compiled CUDA extensions do not load against the current `torch 2.9.1+cu129` stack.
- Historical code-edit backups are tracked in `changelog.txt`.
