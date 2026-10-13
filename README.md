# OpenWrt AP Studio

Local desktop and Android tools for configuring compatible OpenWrt routers as access points. Arabic interface, configurable network templates, no cloud account required.

[الدليل العربي](docs/README_AR.md) · [Download Android / desktop](https://github.com/Bin-Suhail/openwrt-ap-studio/releases/latest) · [Report a bug](https://github.com/Bin-Suhail/openwrt-ap-studio/issues)

## Features

- Use any network name and custom Wi-Fi / hostname templates: `{network}_{number}_{band}` and `AP-{number}`.
- LAN access point or tagged VLAN Wi-Fi clients; management stays untagged on the LAN bridge.
- Independent management IPv4 address, CIDR prefix `/1`–`/30`, optional gateway and IPv4 DNS.
- Arabic, Persian and English digits; device numbers up to 12 digits; VLAN IDs 1–4094.
- Optional WAN bridge membership, optional Wi-Fi client isolation and optional Watchcat removal. These options start **off**.
- DHCP, Dynamic DHCP, DHCPv6 and RA servers are disabled on the access point. The upstream network supplies client addresses.
- Read-only inspection, change review, device identity checks, original configuration backups and rollback while checking the first network reload.
- Android works independently using Wi-Fi or USB Ethernet. Desktop uses Python's standard library.

## Supported configurations

This configures **OpenWrt**, not stock vendor firmware, and does not flash firmware. It supports a static `lan` management interface using a modern `device` bridge and existing AP Wi-Fi interfaces. It detects board and port information instead of selecting hardware by brand.

Existing `swconfig`, `bridge-vlan`, VLAN filtering or custom routing layouts are rejected. It does not configure the upstream router. For VLAN mode, the uplink must already carry that tag and provide DHCP on it. Wired ports remain on the untagged management LAN; only selected Wi-Fi interfaces use the client VLAN.

Only devices and networks you administer should be configured. HTTP LuCI/RPC must be available on a trusted local connection. A desktop or phone may need a manual IP when DHCP is disabled.

## Quick start

**Android:** download the signed APK from Releases, install it and connect to your router. Select Wi-Fi or USB Ethernet at the top, enter its current IP and credentials, then read, customize, review and apply. Android 7+; ARM64 and x86_64. The public app has a separate application ID from earlier private builds.

**Windows:** install Python 3.10+, extract the desktop download and run `desktop/START.cmd`.

**Linux / macOS:** run `sh desktop/start.sh` with Python 3.10+.

The default mode is LAN and the management IP is preserved after reading. Enable automatic IP numbering only if your network uses that scheme. Choose a unique address within your network before applying it.

```sh
python desktop/app/server.py
# Offline synthetic demonstration, no router access or writes:
python desktop/app/server.py --demo
```

## Development

```sh
python -m unittest discover -s tests -v
python scripts/sync_android.py --check
```

Edit shared code in `desktop/app`, then run `python scripts/sync_android.py`. Android includes the same backend and interface; `android_entry.py` provides the native transport.

Android build: JDK 17, Android SDK 36, Python 3.12, Gradle wrapper 9.3.1. Configure the SDK using `ANDROID_HOME` or a private `android/local.properties`, then:

```sh
cd android
sh gradlew assembleDebug
```

For a signed release set `APSTUDIO_KEYSTORE`, `APSTUDIO_STORE_PASSWORD`, `APSTUDIO_KEY_ALIAS` and `APSTUDIO_KEY_PASSWORD` in your private build environment, then run `assembleRelease`. Never commit the keystore or its passwords. Without these variables the release build is unsigned. Keep your signing key to update installed copies.

## Privacy and validation

Router passwords are not saved. An optional saved naming profile contains only templates and IP-generation preferences. Original configuration backups **can contain existing Wi-Fi keys**; keep them private. Do not post backups, real device dumps, credentials or signing keys in issues.

Unit tests use synthetic RPC fixtures. Automated tests and a successful APK build do not replace testing a particular router's drivers or client connectivity. Device support is based on its actual configuration, not an unrestricted promise to support every OpenWrt layout.

MIT licensed. Bundled Gradle wrapper, Android/Python runtime and build dependencies retain their respective licenses; see [third-party notices](THIRD_PARTY_NOTICES.md). This is an independent project, not an official OpenWrt application.
