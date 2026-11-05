# Third-party software

The owner confirmed authorship and publication rights for the gateway, workflow, and reviewer bridge. No upstream Git histories are imported. The worker supervisor is part of the local reviewer/gateway integration. Its source contains no third-party attribution or vendored code; it is included under the project MIT license.

Python license metadata for every locked dependency is recorded in docs/PYTHON_DEPENDENCIES.json. This metadata inventory is not a substitute for upstream license texts when redistributing installed dependencies.

Python dependencies are identified by exact version in gateway/requirements.lock.txt. The gateway directly imports aiohttp, httpx, and LiteLLM; their upstream license files and transitive dependency notices remain in installed distributions. Reviewer dependencies are @modelcontextprotocol/sdk (MIT) and zod (MIT), pinned with transitive dependencies in reviewer/pnpm-lock.yaml. Review upstream metadata before redistributing any dependencies.

Claude Code, Codex, Python, Node.js, pnpm, REA, Android SDK, Java, JADX, Ghidra, and provider services are separately installed or accessed and are not included. Their names identify integrations and imply no endorsement.

Project source is licensed under MIT, copyright (c) 2026 teavert. Dependency licenses are separate and remain with their upstream distributions. No installed dependency code, executable, plugin cache, or upstream Git history is distributed in this repository.
