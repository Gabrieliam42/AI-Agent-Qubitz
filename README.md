# Qubitz

Exhausted of testing relatively small LLMs with AI agents that often ignore instructions, misuse tools, or wander off task?

Qubitz is a local-first AI agent with a specialized harness and Agent Behavioral Contracts, that aims to make 7B–35B MCP/tool-capable LLMs more predictable and useful. It keeps routing, workspace handling, retrieval, and tool orchestration under wrapper control, so smaller models are not left to decide everything on their own.

***
Qubitz is a standalone local-only AI agent for GGUF models on `llama.cpp`. It is oriented to local LLM workflows only: no cloud inference, no subscriptions, and no paid hosted services are required.

Qubitz is unusual compared to most AI agents because it is strongly local-first, Agentic Behavioral Contracts Wrapper-driven and Harness-driven.

![MasterHead](https://i.imgur.com/mDfL5vB.png)

Most agents out there are one of these:

- Cloud/API agents: faster setup, stronger frontier models, but dependent on APIs, subscriptions, cloud state, and vendor limits.
- IDE agents: good UX and repo integration, but usually tied to a hosted model or editor ecosystem.
- Local chat wrappers: private/local, but often weak as real agents because tool routing, workspace handling, and recovery paths are thin.
- Research agent frameworks: flexible, but often overcomplicated, brittle, and not optimized for one real workstation.

Qubitz is closer to a local style agent with a strong harness. Its stronger points are:

- Fully local orientation: no API, no cloud, no subscription dependency.
- Multiple model variants: lets you compare behavior across 8B-35B-class local models.
- The LLMs's retrieval source is mostly `Unsloth Dynamic`, on `HuggingFace.co`.
- Wrapper-owned routing: simple questions, direct existing scripts, read-only workspace tasks, and tool/MCP paths are not left entirely to the model.
- Good WSL2/Windows awareness: this is a real advantage because many agents handle mixed Windows/WSL workspaces badly.
- Strong direct-entrypoint path: this is better than many agents that overthink and rewrite instead of running what already exists.
- Harness plus wrapper separation: useful because small models need both persistent policy and runtime facts.
- Local retrieval/embeddings: gives project context without cloud retrieval.
- Agentic Behavioral Contracts based.

A realistical view: For its purposes Qubitz is better than most local hobby agents and many generic framework agents for practical local repository work. It is not better than frontier cloud coding agents on raw intelligence, but it is much better if your priorities are privacy, no paid services, local control, WSL/Windows operation, and predictable wrapper-owned behavior.

The most valuable design choice is that Qubitz does not let small models decide everything. The wrapper owns routing, execution facts, and fast paths; the model handles language/reasoning where needed. That is the right architecture for 7B-35B local agents.

It is intended to run primarily under WSL2/Linux, and to work in WSL-hosted workspaces and Windows-hosted workspaces accessed through the WSL-to-Windows bridge.

## What it includes

Key Features

Local-First Architecture:
 Unlike typical AI agents that may depend on cloud infrastructure, Qubitz operates entirely in local environments, ensuring best data privacy and reduced latency.
Harness and Wrapper Control:
 The architecture utilizes a unique harness that manages routing, workspace handling, and tool orchestration, allowing smaller models to focus on language and reasoning.
Multiple Model Variants:
 Qubitz supports various local models, enabling users to compare performance and capabilities among different configurations ranging from 8B to 35B.
Direct Execution Capability:
 It allows for executing existing scripts and commands across different environments such as Windows and WSL2 with enhanced control over task routing and execution logic.
GUI, CLI, and MCP Integration: Users can interact through a graphical interface or command line, further simplifying the engagement with AI-driven tasks.


Practical Advantages

Qubitz offers practical advantages for users who prioritize local control over their AI workflows, especially in mixed operating system environments. 
Its strong focus on routing and task execution creates a more seamless interaction compared to other frameworks that often do not cater effectively to local requirements.

- Local `llama.cpp` GGUF generation
- Task-routed workspace retrieval with local embedding models
- Tk GUI, CLI, and stdio MCP server modes
- Direct existing-entrypoint execution for explicit `.py`, `.ps1`, `.sh`, `.bat`, `.cmd`, `uv run`, `npm run`, `pnpm run`, and `make` tasks
- Local background jobs, local plugin guidance, and wrapper-local sandbox/tool orchestration

## All variant scripts are using embedding model `BAAI's` BGE-Code-v1 along with their main model.

- `AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.py` - for 12GB VRAM GPU `Qwen Team's` Qwen-3.5-9B Q5 Dense
- `AI_Agent_Qubitz_AgenticQwen-8B.i1-Q5_12G.py` - for 12GB VRAM GPU `Qwen Team's` AgenticQwen-8B Q5 Dense
- `AI_Agent_Qubitz_Granite-4.1-8B_Q5_12G.py` - for 12GB VRAM GPU `IBM's` Granite-4.1-8B Q5 Dense
*****
- `AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py` - 24 GB VRAM GPU `Z.AI's` GLM-4.7-Flash 30B A3B MoE Q4
- `AI_Agent_Qubitz_North-Mini-Code-1.0-30B-A3B-Q4.py` - 24 GB VRAM GPU `Cohere's` North-Mini-Code-1.0 30B A3B MoE Q4
- `AI_Agent_Qubitz_Nemotron-Cascade-2-30B-A3B-IQ4.py` - 24 GB VRAM GPU `NVIDIA's` Nemotron-Cascade-2 30B A3B MoE IQ4
- `AI_Agent_Qubitz_Nemotron-3-Nano-30B-A3B-IQ4.py` - 24 GB VRAM GPU `NVIDIA's` Nemotron-3-Nano 30B A3B MoE IQ4
- `AI_Agent_Qubitz_GPT-OSS-20B_F16.py` - 24 GB VRAM GPU `OpenAI's` GPT-OSS-20B F16 A3B MoE F16
- `AI_Agent_Qubitz_Ornith-1.0-35B-A3B-Q4.py` - 24 GB VRAM GPU `DeepReinforce AI's` Ornith-1.0-35B A3B MoE Q4
- `AI_Agent_Qubitz_AgenticQwen-30B-A3B.i1-Q4.py` - 24 GB VRAM GPU `Qwen Team's` AgenticQwen-30B A3B MoE Q4
- `AI_Agent_Qubitz_Qwen3.6-35B-A3B_Q4.py` - 24 GB VRAM GPU `Qwen Team's` Qwen-3.6-35B A3B MoE Q4
- `AI_Agent_Qubitz_Gemma-4-31B-IT_QAT-Q4.py` - 24 GB VRAM GPU `Google's` Gemma-4-31B-IT-QAT Q4 Dense
- `AI_Agent_Qubitz_Qwen3.6-27B_Q4.py` - 24 GB VRAM GPU `Qwen Team's` Qwen-3.6-27B Q4 Dense
- `AI_Agent_Qubitz_Granite-4.1-8B_Q8.py` - 24 GB VRAM GPU `IBM's` Granite-4.1-8B Q8
- `AI_Agent_Qubitz_Devstral-Small-2-24B-Q4.py` - 24 GB VRAM GPU `Mistral AI's` Devstral-Small-2 24B Q4 Dense
- `AI_Agent_Qubitz_Qwen3.5_9B_Q8.py` - 24 GB VRAM GPU `Qwen Team's` Qwen-3.5-9B Q8 Dense
- `AI_Agent_Qubitz_AgenticQwen-8B-F16.py` - 24 GB VRAM GPU `Qwen Team's` AgenticQwen-8B F16 Dense

Legend:
- MoE = Mixture of Experts
- A3B = Approximately 3 billion parameters are activated per token from the model's total parameters through its Mixture-of-Experts routing.
- QAT = Quantization-Aware Training (trained to better preserve quality after quantization)
- IT = Instruction-Tuned
- Q4/Q5/Q6/Q8 = Approximate quantization bit-width
- F16/FP16 = 16-bit floating-point precision
- IQ4 = Importance-aware 4-bit quantization
- i1 = Importance-matrix-weighted quantization
- 12G = Variant intended for approximately 12 GB of VRAM


## Runtime behavior

- Short simple questions use a fast path that skips broader retrieval, embedding generation, and local skill/MCP expansion.
- Repository-specific, code-specific, and multi-step tasks use retrieval when needed.
- If a prompt explicitly names an existing project entrypoint, the wrapper can run it directly and return the result without forcing a slower model/tool loop.
- Wrapper-provided runtime facts steer WSL/Windows execution behavior so small local models do not need to infer interop rules from scratch.

## Harness behavior

- It uses `HARNESS.enc` that exists in the workspace root
- `HARNESS.enc` is excluded from normal retrieval context paths so the encrypted duplicate is not injected.
- `QUBITZ_HARNESS_KEY.local.txt` is also used for the harness loading.

## Main files

- Variant scripts: the eight `AI_Agent_Qubitz_*.py` files above
- `HARNESS.enc` - AI Agent Harness
- `QUBITZ_HARNESS_KEY.local.txt` - local harness-key helper file
- `requirements.txt` - runtime dependencies
- `requirements-ci.txt` - CI, lint, and test dependencies

## Setup

From the Windows project directory, create and use a WSL2/Linux virtual environment:

```powershell
wsl python3 -m venv .venv
wsl .venv/bin/pip install -r requirements.txt
```

Launch a variant:

```powershell
wsl .venv/bin/python AI_Agent_Qubitz_GLM_4_7_Flash.py
```

CLI examples:

```powershell
wsl .venv/bin/python AI_Agent_Qubitz_Qwen3.5_9B_Q8_12G.py --cli
wsl .venv/bin/python AI_Agent_Qubitz_Qwen3.5_9B_Q8_12G.py --cli --prompt "What does this project do?"
```

MCP server example:

```powershell
wsl .venv/bin/python AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py --serve-mcp
```

If you already have a compatible `llama.cpp` server or GGUF path, point a variant at it with `--server-url`, `--llama-server`, and `--model-path`.

## Important options

- `--num-ctx`
- `--num-predict`
- `--max-steps`
- `--thinking-effort` with `default`, `low`, `medium`, `high`, or `xhigh`

In the GUI, the lower-right `Effort` selector maps to the same preset.
