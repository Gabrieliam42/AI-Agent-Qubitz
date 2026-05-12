# Qubitz

Qubitz is a local-first standalone AI agent for repository work, tool use, and controlled file operations. The current implementation is centered in `AI_Agent_Qubitz.py` and combines a Tk desktop UI, a CLI mode, a local MCP server/client loop, Ollama-based reasoning, encrypted harness loading, local skill discovery, and persistent memory.

## Current Runtime

- Platform target: WSL2 on Windows 11
- Python target: 3.12
- GPU target: RTX 3090 24 GB VRAM
- Main model path: `glm-4.7-flash:latest` through Ollama
- Runtime defaults: `num_ctx=16384`, `num_predict=4096`, `max_steps=16`

## Main Capabilities

- Tk-based local desktop interface
- CLI mode for one-shot prompts or interactive terminal use
- Local MCP server/client loop for bounded tool execution
- Workspace-aware file read, write, replace, move, delete, and search tools
- Encrypted harness support via `HARNESS.enc` and `QUBITZ_HARNESS_KEY`
- Optional project rules loading from `RULES.md`
- Local skill discovery from `.skills/*/SKILL.md` when present
- Persistent session memory under `.memory/`
- WSL-first Ollama access with direct localhost preference and Windows bridge fallback

## Repository Files

- `.gitignore`: git exclusions for local-only artifacts, plaintext secrets, and runtime data
- `AI_Agent_Qubitz.py`: main standalone agent runtime
- `HARNESS.enc`: encrypted harness tracked in the repository
- `requirements.txt`: runtime dependencies
- `README.md`: public repository overview and runtime notes

## Optional Local Runtime Files And Paths

- `HARNESS.txt`: optional plaintext harness source for maintainers who need to re-encrypt the tracked harness
- `RULES.md`: optional local project rules file loaded after the harness when present
- `QUBITZ_HARNESS_KEY.local.txt`: optional ignored helper file for local key storage
- `.cache/`: local cache directory used for project-scoped runtime data
- `.memory/`: current and archived session memory
- `.skills/`: local skill directories
- `.venv/`: WSL Python virtual environment

## Dependency Notes

The active runtime is built around:

- `mcp==1.27.0`
- `httpx==0.28.1`
- `cryptography==47.0.0`
- `torch==2.9.1+cu129`
- `transformers==4.57.6`
- `flash-attn==2.8.3`

Additional environment notes:

- `requirements.txt` includes `cryptography` because encrypted harness support is part of the tracked runtime.
- `torchvision` and `torchaudio` may be present in a local environment, but they are not required by the current tracked runtime path.
- `xformers==0.0.31.post1` may exist in a local environment, but its compiled CUDA extensions are not compatible with the current `torch 2.9.1+cu129` / Python 3.12 stack.

## Status

- The current runtime is concentrated in a single main file instead of a multi-module package layout.
- Explicit file-read requests are handled deterministically before the model loop when possible.
- The local MCP server exposes skill-aware resources and tools, including `skills://index`, `memory://current`, `list_skills`, `read_skill`, `read_skill_resource`, `read_memory`, and `search_memory`.
- The repository tracks `HARNESS.enc` instead of plaintext harness content, so startup requires `QUBITZ_HARNESS_KEY` to be available in the runtime environment.
