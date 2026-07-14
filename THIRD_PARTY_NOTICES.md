# Third-party notices

## XHS-Downloader

- Project: JoeanAmier/XHS-Downloader
- Source: https://github.com/JoeanAmier/XHS-Downloader
- Pinned release: 2.7
- Pinned commit: `afaf2fb459980fccef9eec74e304a39af2c49cab`
- License: GNU General Public License v3.0

XHS-Downloader is downloaded from its upstream repository into the user's local application-data directory and invoked as a separate process. Its source and binaries are not copied into the MIT-licensed CollectHub repository or release archive. Users receive the upstream license with the installed source.

## gallery-dl

- Project: mikf/gallery-dl
- Source: https://github.com/mikf/gallery-dl
- Pinned release: 1.32.1
- License: GPL-2.0

## yt-dlp

- Project: yt-dlp/yt-dlp
- Source: https://github.com/yt-dlp/yt-dlp
- Pinned release: 2026.06.09
- License: Unlicense

Both X tools are installed from their upstream Python distributions into an isolated user-local runtime and invoked as separate processes. They are not bundled into the MIT-licensed CollectHub repository or release archive.

## uv

- Project: astral-sh/uv
- Source: https://github.com/astral-sh/uv
- Pinned release: 0.11.7
- License: Apache-2.0 OR MIT

The installer downloads the platform-specific uv archive for the pinned release and verifies an embedded upstream SHA-256 checksum before extraction. uv installs the isolated Python 3.12 runtime and locked Python dependencies without modifying the system Python.
