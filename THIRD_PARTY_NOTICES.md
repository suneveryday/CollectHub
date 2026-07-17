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
- Pinned release: 2026.07.04
- Verified wheel SHA-256: `f11f2b11d5a8ac4059f9bdf29fa4407dc7c6bb00c5097e95ca22a7a9db518266`
- License: Unlicense

## yt-dlp-ejs

- Project: yt-dlp/ejs
- Source: https://github.com/yt-dlp/ejs
- Pinned release: 0.8.0, selected by `yt-dlp[default,pin]` 2026.07.04
- License: Unlicense; prebuilt wheels also contain ISC- and MIT-licensed components

gallery-dl, yt-dlp, and yt-dlp-ejs are installed from their upstream Python distributions into an isolated user-local runtime and invoked by CollectHub without being copied into the MIT source archive.

## Deno

- Project: denoland/deno
- Source: https://github.com/denoland/deno
- Pinned release: 2.8.1
- License: MIT

The installer downloads the architecture-specific macOS archive and verifies its embedded upstream SHA-256 checksum. Deno is used only as yt-dlp's external JavaScript challenge runtime.

## Trafilatura

- Project: adbar/trafilatura
- Source: https://github.com/adbar/trafilatura
- Pinned release: 2.1.0
- License: Apache-2.0

Trafilatura and its locked transitive dependencies are installed in CollectHub's isolated Python environment. Their licenses remain independent from CollectHub's MIT license.

## uv

- Project: astral-sh/uv
- Source: https://github.com/astral-sh/uv
- Pinned release: 0.11.7
- License: Apache-2.0 OR MIT

The installer downloads the platform-specific uv archive for the pinned release and verifies an embedded upstream SHA-256 checksum before extraction. uv installs the isolated Python 3.12 runtime and locked Python dependencies without modifying the system Python.
