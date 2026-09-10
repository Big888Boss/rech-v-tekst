#!/bin/bash
set -e

echo "Building macOS application..."
source .venv/bin/activate
pyinstaller build.spec --clean -y

echo "Creating checksums..."
cd dist
shasum -a 256 "Речь в текст.app/Contents/MacOS/macos_app" > checksum.txt
echo "Checksum saved to dist/checksum.txt"

echo "Build complete."
