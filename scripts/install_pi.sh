#!/usr/bin/env bash
# Raspberry Pi 4 / 5, Raspberry Pi OS 64-bit (Bookworm). Run from the fd-benchmark folder.
set -e
cd "$(dirname "$0")/.."
sudo apt update
sudo apt install -y python3-venv python3-dev libportaudio2 libsndfile1 pulseaudio-utils
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
echo
echo "Core installed. Optional (slow on a Pi 4):  pip install faster-whisper praat-parselmouth"
echo "Next:  bash scripts/setup_virtual_audio.sh   then   python -m fdbench"
