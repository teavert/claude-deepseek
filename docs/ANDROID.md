# Optional Android and REA integration

The base workflow does not require Android tools. Install REA and its upstream skill separately using upstream instructions. Install Android SDK, Java, JADX, and Ghidra only as needed, under their own licenses.

Add the REA MCP server to ignored config/mcp.json using its installed executable and paths; merge it alongside codex-subscription. Add project-specific toolchain variables to your shell environment. Keep device identifiers, private artifact paths, and local toolchain files out of Git. No REA plugin caches, skills, SDK binaries, or device data are redistributed here.
