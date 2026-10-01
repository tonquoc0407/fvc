#!/usr/bin/env bash
# Build a Debian package for Finnvnoi API Check.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VERSION:-1.0.2}"
OUT="$ROOT/dist"
PKG="$OUT/finnvnoi-api-check_${VERSION}_all"

rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/finnvnoi-api-check" "$PKG/usr/bin" "$PKG/usr/share/applications"
cp -R "$ROOT/codex_usage" "$ROOT/entry_tui.py" "$ROOT/pyproject.toml" "$ROOT/README.md" \
   "$PKG/opt/finnvnoi-api-check/"
find "$PKG" -type d -name __pycache__ -prune -exec rm -rf {} +
cat > "$PKG/usr/bin/fvc" <<'EOF'
#!/bin/sh
export PYTHONPATH="/opt/finnvnoi-api-check${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m codex_usage "$@"
EOF
chmod 755 "$PKG/usr/bin/fvc"
cat > "$PKG/usr/share/applications/fvc.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Finnvnoi API Check
Comment=Check Finnvnoi API usage
Exec=fvc tui
Terminal=true
Categories=Utility;
EOF
cat > "$PKG/DEBIAN/control" <<EOF
Package: finnvnoi-api-check
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.9)
Maintainer: Finnvnoi
Description: Finnvnoi API usage checker
 Check API key usage from the terminal. The app asks for an API key on first start.
EOF
dpkg-deb --build --root-owner-group "$PKG" "$OUT/finnvnoi-api-check_${VERSION}_all.deb"
rm -rf "$PKG"
echo "Built $OUT/finnvnoi-api-check_${VERSION}_all.deb"
