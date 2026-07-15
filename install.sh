#!/bin/sh
set -eu

VERSION=1.1.2
UV_VERSION=0.11.7
REPOSITORY=suneveryday/CollectHub
CLIENT=auto
YES=0
DRY_RUN=0
REPAIR=0
UNINSTALL=0
SKIP_RUNTIMES=0
SOURCE_DIR=

usage() {
  echo "Usage: install.sh [--yes] [--dry-run] [--repair] [--uninstall]"
  echo "                  [--client auto|codex|hermes|all] [--source DIR]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes) YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --repair) REPAIR=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --client)
      shift
      [ "$#" -gt 0 ] || { usage >&2; exit 2; }
      CLIENT=$1
      ;;
    --source)
      shift
      [ "$#" -gt 0 ] || { usage >&2; exit 2; }
      SOURCE_DIR=$1
      ;;
    --skip-runtimes) SKIP_RUNTIMES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[ "$(uname -s)" = Darwin ] || {
  echo "CollectHub v$VERSION currently supports macOS only." >&2
  exit 1
}
case "$(uname -m)" in
  arm64|x86_64) ;;
  *) echo "Unsupported macOS architecture: $(uname -m)" >&2; exit 1 ;;
esac
case "$CLIENT" in
  auto|codex|hermes|all) ;;
  *) echo "Invalid client: $CLIENT" >&2; exit 2 ;;
esac

APP_ROOT=${COLLECTHUB_APP_ROOT:-"$HOME/.local/share/collecthub"}
RELEASES=$APP_ROOT/releases
RELEASE_DIR=$RELEASES/$VERSION
CURRENT=$APP_ROOT/current
BIN_DIR=${COLLECTHUB_BIN_DIR:-"$HOME/.local/bin"}
CLI=$BIN_DIR/content-ingestor
BACKUPS=$APP_ROOT/backups
STAMP=$(date +%Y%m%d-%H%M%S)

clients() {
  case "$CLIENT" in
    codex) echo codex ;;
    hermes) echo hermes ;;
    all) printf '%s\n' codex hermes ;;
    auto)
      FOUND=0
      if [ -d "$HOME/.codex" ] || command -v codex >/dev/null 2>&1; then
        echo codex
        FOUND=1
      fi
      if [ -d "$HOME/.hermes" ] || command -v hermes >/dev/null 2>&1; then
        echo hermes
        FOUND=1
      fi
      [ "$FOUND" -eq 1 ] || echo codex
      ;;
  esac
}

skill_target() {
  case "$1" in
    codex) echo "${CODEX_HOME:-$HOME/.codex}/skills/content-ingestor" ;;
    hermes) echo "${HERMES_HOME:-$HOME/.hermes}/skills/content-ingestor" ;;
  esac
}

legacy_hermes_skill_target() {
  echo "${HERMES_HOME:-$HOME/.hermes}/skills/social-media/content-ingestor"
}

echo "CollectHub v$VERSION"
echo "Application: $RELEASE_DIR"
echo "CLI: $CLI"
echo "Library: ${COLLECTHUB_LIBRARY:-$HOME/CollectHub}"
echo "Clients:"
clients | while IFS= read -r item; do
  echo "  $item -> $(skill_target "$item")"
  if [ "$item" = hermes ] && [ -e "$(legacy_hermes_skill_target)" ]; then
    echo "    legacy skill -> backup outside the scanned skill tree"
  fi
done

if [ "$UNINSTALL" -eq 1 ]; then
  echo "Action: uninstall application and managed skills; preserve the local library"
else
  echo "Dependencies: uv $UV_VERSION, Python 3.12, XHS-Downloader 2.7, gallery-dl 1.32.1, yt-dlp[default,pin] 2026.06.09, EJS 0.8.0, Deno 2.8.1, Trafilatura 2.1.0"
  echo "Licenses: CollectHub MIT; external downloader licenses are listed in THIRD_PARTY_NOTICES.md"
  echo "Action: install or update"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run only. No files changed."
  exit 0
fi

if [ "$YES" -ne 1 ]; then
  if [ ! -r /dev/tty ]; then
    echo "No interactive terminal. Re-run with --yes after reviewing the summary." >&2
    exit 2
  fi
  printf "Continue? [y/N] " >/dev/tty
  IFS= read -r answer </dev/tty || answer=
  case "$answer" in y|Y|yes|YES) ;; *) echo "Cancelled."; exit 0 ;; esac
fi

mkdir -p "$BACKUPS/skills" "$BIN_DIR"

if [ "$UNINSTALL" -eq 1 ]; then
  clients | while IFS= read -r item; do
    target=$(skill_target "$item")
    if [ -f "$target/.collecthub-managed" ]; then
      mkdir -p "$BACKUPS/skills/$item"
      mv "$target" "$BACKUPS/skills/$item/content-ingestor-$STAMP"
      echo "Removed managed $item skill"
    fi
  done
  if [ -f "$CLI" ] && grep -q 'collecthub-managed' "$CLI"; then
    rm -f "$CLI"
  fi
  if [ -e "$APP_ROOT" ]; then
    destination="$HOME/.local/share/collecthub-uninstalled-$STAMP"
    mv "$APP_ROOT" "$destination"
    echo "Application moved to: $destination"
  fi
  echo "Local library preserved: ${COLLECTHUB_LIBRARY:-$HOME/CollectHub}"
  exit 0
fi

TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/collecthub-install.XXXXXX")
cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT INT TERM

if [ -z "$SOURCE_DIR" ]; then
  script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || true)
  if [ -f "$script_dir/VERSION" ] \
    && [ "$(cat "$script_dir/VERSION")" = "$VERSION" ] \
    && [ -f "$script_dir/skills/content-ingestor/SKILL.md" ]; then
    SOURCE_DIR=$script_dir
  else
    archive=$TMP_ROOT/collecthub-$VERSION.tar.gz
    checksum=$TMP_ROOT/collecthub-$VERSION.tar.gz.sha256
    base="https://github.com/$REPOSITORY/releases/download/v$VERSION"
    curl --proto '=https' --tlsv1.2 -fsSL "$base/collecthub-$VERSION.tar.gz" -o "$archive"
    curl --proto '=https' --tlsv1.2 -fsSL "$base/collecthub-$VERSION.tar.gz.sha256" -o "$checksum"
    (cd "$TMP_ROOT" && shasum -a 256 -c "$(basename "$checksum")")
    mkdir -p "$TMP_ROOT/source"
    tar -xzf "$archive" -C "$TMP_ROOT/source"
    SOURCE_DIR=$TMP_ROOT/source
  fi
fi
[ -f "$SOURCE_DIR/pyproject.toml" ] || { echo "Invalid CollectHub source: $SOURCE_DIR" >&2; exit 1; }

mkdir -p "$RELEASES"
if [ -e "$RELEASE_DIR" ] && [ "$REPAIR" -eq 1 ]; then
  mv "$RELEASE_DIR" "$BACKUPS/release-$VERSION-$STAMP"
fi
if [ ! -e "$RELEASE_DIR" ]; then
  staging=$RELEASES/.install-$VERSION-$$
  mkdir -p "$staging"
  for entry in VERSION LICENSE README.md THIRD_PARTY_NOTICES.md install.sh pyproject.toml uv.lock src scripts skills; do
    [ -e "$SOURCE_DIR/$entry" ] && cp -R "$SOURCE_DIR/$entry" "$staging/"
  done

  if [ "$SKIP_RUNTIMES" -ne 1 ]; then
    UV=$(command -v uv || true)
    if [ -z "$UV" ] || [ "$("$UV" --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]; then
      case "$(uname -m)" in
        arm64)
          uv_asset=uv-aarch64-apple-darwin.tar.gz
          uv_sha256=66e37d91f839e12481d7b932a1eccbfe732560f42c1cfb89faddfa2454534ba8
          ;;
        x86_64)
          uv_asset=uv-x86_64-apple-darwin.tar.gz
          uv_sha256=0a4bc8fcde4974ea3560be21772aeecab600a6f43fa6e58169f9fa7b3b71d302
          ;;
      esac
      uv_archive=$TMP_ROOT/$uv_asset
      curl --proto '=https' --tlsv1.2 -fsSL \
        "https://releases.astral.sh/github/uv/releases/download/$UV_VERSION/$uv_asset" \
        -o "$uv_archive"
      printf '%s  %s\n' "$uv_sha256" "$uv_archive" | shasum -a 256 -c -
      mkdir -p "$APP_ROOT/tools"
      tar -xzf "$uv_archive" --strip-components=1 -C "$APP_ROOT/tools"
      UV=$APP_ROOT/tools/uv
      [ "$("$UV" --version | awk '{print $2}')" = "$UV_VERSION" ] || {
        echo "Installed uv version does not match $UV_VERSION" >&2
        exit 1
      }
    fi
    PATH="$(dirname "$UV"):$PATH"
    export PATH
    (cd "$staging" && "$UV" sync --locked --python 3.12 --no-editable)
    CONTENT_OS_XHS_HOME="$APP_ROOT/runtimes/xhs-downloader/2.7" \
      "$staging/scripts/install-xhs-downloader" --apply
    COLLECTHUB_MEDIA_RUNTIME_HOME="$APP_ROOT/runtimes/media/gallery-dl-1.32.1_yt-dlp-2026.06.09_deno-2.8.1" \
      "$staging/scripts/install-media-runtime" --apply
  fi
  mv "$staging" "$RELEASE_DIR"
fi

next_link=$APP_ROOT/.current-$STAMP
ln -s "$RELEASE_DIR" "$next_link"
mv -h "$next_link" "$CURRENT"

printf '%s\n' \
  '#!/bin/sh' \
  '# collecthub-managed' \
  'set -eu' \
  'APP_ROOT=${COLLECTHUB_APP_ROOT:-"$HOME/.local/share/collecthub"}' \
  'export PATH="$APP_ROOT/tools:$PATH"' \
  'export CONTENT_OS_XHS_HOME=${CONTENT_OS_XHS_HOME:-"$APP_ROOT/runtimes/xhs-downloader/2.7"}' \
  'export COLLECTHUB_MEDIA_RUNTIME_HOME=${COLLECTHUB_MEDIA_RUNTIME_HOME:-${CONTENT_OS_X_RUNTIME_HOME:-"$APP_ROOT/runtimes/media/gallery-dl-1.32.1_yt-dlp-2026.06.09_deno-2.8.1"}}' \
  'export CONTENT_OS_X_RUNTIME_HOME=${CONTENT_OS_X_RUNTIME_HOME:-"$COLLECTHUB_MEDIA_RUNTIME_HOME"}' \
  'exec "$APP_ROOT/current/.venv/bin/python" -m content_ingestor.cli "$@"' \
  > "$CLI"
chmod 755 "$CLI"

clients | while IFS= read -r item; do
  target=$(skill_target "$item")
  if [ "$item" = hermes ]; then
    legacy_target=$(legacy_hermes_skill_target)
    if [ -e "$legacy_target" ] && [ "$legacy_target" != "$target" ]; then
      mkdir -p "$BACKUPS/skills/$item"
      mv "$legacy_target" "$BACKUPS/skills/$item/content-ingestor-legacy-$STAMP"
      echo "Backed up legacy Hermes skill: $legacy_target"
    fi
  fi
  if [ -e "$target" ]; then
    mkdir -p "$BACKUPS/skills/$item"
    mv "$target" "$BACKUPS/skills/$item/content-ingestor-$STAMP"
  fi
  mkdir -p "$(dirname "$target")"
  cp -R "$RELEASE_DIR/skills/content-ingestor" "$target"
  : > "$target/.collecthub-managed"
  echo "Installed $item skill: $target"
done

echo "Installed CollectHub v$VERSION"
echo "Run: $CLI doctor"
echo "Restart Codex or restart the Hermes Gateway to load the skill."
