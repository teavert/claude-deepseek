# Third-party software

The owner confirmed authorship and publication rights for the gateway, workflow, and reviewer bridge. No upstream Git histories are imported. The worker supervisor is exported from the same local integration; include it in the final provenance and license review before public release.

Python license metadata for every locked dependency is recorded in docs/PYTHON_DEPENDENCIES.json. This metadata inventory is not a substitute for upstream license texts when redistributing installed dependencies.

Python dependencies are identified by exact version in gateway/requirements.lock.txt. The gateway directly imports aiohttp, httpx, and LiteLLM; their upstream license files and transitive dependency notices remain in installed distributions. Reviewer dependencies are @modelcontextprotocol/sdk (MIT) and zod (MIT), pinned with transitive dependencies in reviewer/pnpm-lock.yaml. Review upstream metadata before redistributing any dependencies.

Claude Code, Codex, Python, Node.js, pnpm, REA, Android SDK, Java, JADX, Ghidra, and provider services are separately installed or accessed and are not included. Their names identify integrations and imply no endorsement.

A project license has not been chosen. This preparation repository is not yet a licensed public open-source release. Add the selected license and copyright holder before publication.
