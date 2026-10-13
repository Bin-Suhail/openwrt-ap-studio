# Security and privacy

Use the tool only on a trusted local network and on routers you administer. LuCI/RPC uses HTTP in this release; use a direct cable or a trusted isolated Wi-Fi connection. Do not expose the local UI to the Internet.

The UI binds only to `127.0.0.1`, checks Host/Origin and requires a per-run token. Android binds router requests to the chosen local Wi-Fi/Ethernet network, without following redirects or using cellular/VPN fallback.

Passwords are held only for an active router session. Configuration backups may include original Wi-Fi keys. Profiles deliberately exclude passwords and Wi-Fi keys. Never commit backups, device dumps, SDK-local configuration or publisher signing keys.

For a security report, provide a minimal synthetic reproduction without private information. If private vulnerability reporting is enabled on this repository, use **Security → Report a vulnerability**. Avoid publishing credentials or active exploitation details in public issues.
