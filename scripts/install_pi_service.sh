#!/usr/bin/env bash
# Run the assistant in benchmark mode as a systemd service on the Pi, so it reconnects to fdbench for every run
# and comes back after a reboot.   bash scripts/install_pi_service.sh ws://LAPTOP-IP:8791
set -e
URL="${1:?usage: install_pi_service.sh ws://LAPTOP-IP:8791}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"          # fd-benchmark
PROJECT="$(cd "$HERE/.." && pwd)"                   # the folder that holds assistant/
PY="$PROJECT/.venv/bin/python"
[ -x "$PY" ] || { echo "no venv at $PY (run app/install_pi.sh first)"; exit 1; }
sudo tee /etc/systemd/system/fdbench-assistant.service >/dev/null <<UNIT
[Unit]
Description=voice assistant in fd-benchmark mode (connects to $URL)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$PROJECT
ExecStart=$PY $HERE/launch_assistant.py --url $URL --project $PROJECT
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now fdbench-assistant
echo "installed and started. Log: journalctl -u fdbench-assistant -f    Stop: sudo systemctl disable --now fdbench-assistant"
