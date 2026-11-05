#!/bin/bash
# Installation commune Raspberry Pi / Orange Pi.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Relance avec les droits d'administration..."
  exec sudo --preserve-env=MESH_BOARD,MESH_EXTRA_PACKAGES,MESH_PORT bash "$0" "$@"
fi

export DEBIAN_FRONTEND=noninteractive
BOARD="${MESH_BOARD:-Linux}"
EXTRA="${MESH_EXTRA_PACKAGES:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="/opt/meshpass"
PORT="${MESH_PORT:-8080}"
TARGET_USER="${SUDO_USER:-}"
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ]; then
  TARGET_USER="$(logname 2>/dev/null || true)"
fi
if [ -z "$TARGET_USER" ]; then
  TARGET_USER="root"
fi
HOME_DIR="$(getent passwd "$TARGET_USER" | cut -d: -f6)"

echo "Installation Meshpass pour ${BOARD}"
echo "Utilisateur : ${TARGET_USER}"
echo "Dossier : ${DEST}"

apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates ${EXTRA}

mkdir -p "$DEST"
find "$ROOT" -maxdepth 1 -type f \( -name '*.py' -o -name '*.txt' -o -name '*.md' -o -name '*.html' \) -exec cp -a {} "$DEST/" \;
rm -rf "$DEST/gateway" "$DEST/tests" "$DEST/install"
cp -a "$ROOT/gateway" "$ROOT/tests" "$ROOT/install" "$DEST/"
find "$DEST" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
if [ ! -d "$DEST/data" ] && [ -d "$ROOT/data" ]; then
  cp -a "$ROOT/data" "$DEST/data"
fi

python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --upgrade pip
"$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"
chown -R "$TARGET_USER:$TARGET_USER" "$DEST"
usermod -aG dialout "$TARGET_USER" 2>/dev/null || true

cat > /usr/local/bin/meshpass << EOF
#!/bin/bash
case "\${1:-open}" in
  start) systemctl start meshpass ;;
  stop) systemctl stop meshpass ;;
  restart) systemctl restart meshpass ;;
  status) systemctl --no-pager --full status meshpass ;;
  open) xdg-open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || echo "http://127.0.0.1:${PORT}" ;;
  *) echo "Usage : meshpass {start|stop|restart|status|open}" ;;
esac
EOF
chmod 755 /usr/local/bin/meshpass

cat > /etc/systemd/system/meshpass.service << EOF
[Unit]
Description=Passerelle Mesh
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${TARGET_USER}
Group=${TARGET_USER}
SupplementaryGroups=dialout
WorkingDirectory=${DEST}
Environment=MESH_PORT=${PORT}
Environment=MESH_OPEN_BROWSER=0
ExecStart=${DEST}/.venv/bin/python -u run.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now meshpass

LAUNCHER="${HOME_DIR}/.local/share/applications/meshpass.desktop"
mkdir -p "$(dirname "$LAUNCHER")"
cp_desktop=0
if [ -d "${HOME_DIR}/Desktop" ] || mkdir -p "${HOME_DIR}/Desktop" 2>/dev/null; then
  cp_desktop=1
fi

write_desktop() {
  cat > "$1" << EOF
[Desktop Entry]
Type=Application
Name=Passerelle Mesh
Comment=Ouvre la passerelle Meshpass
Exec=bash -lc 'systemctl start meshpass; (xdg-open http://127.0.0.1:${PORT} || true)'
Terminal=false
Categories=Network;
EOF
  chmod 755 "$1"
  chown "$TARGET_USER:$TARGET_USER" "$1"
}

write_desktop "$LAUNCHER"
if [ "${cp_desktop}" = "1" ] && [ -d "${HOME_DIR}/Desktop" ]; then
  write_desktop "${HOME_DIR}/Desktop/Passerelle Mesh.desktop"
fi

echo
echo "Meshpass est installé pour ${BOARD}."
echo "Page : http://127.0.0.1:${PORT}"
echo "Mot de passe initial : meshpass"
echo "Commandes : meshpass status | meshpass open | meshpass restart"
echo "Le lanceur « Passerelle Mesh » est sur le bureau, s'il existe."
echo "Débranchez puis rebranchez la session pour le groupe dialout (ports USB)."
