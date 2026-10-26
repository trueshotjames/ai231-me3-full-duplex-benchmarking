#!/usr/bin/env bash
# Create the two virtual audio cables on Raspberry Pi OS (PipeWire or PulseAudio).
#   cable 1  fdb_to_bot    : the tool plays here, the assistant records from fdb_to_bot.monitor
#   cable 2  fdb_from_bot  : the assistant plays here, the tool records from "Monitor of fdb_from_bot"
# Re-run after every reboot (or add to ~/.config/autostart).
set -e
if ! command -v pactl >/dev/null; then
  echo "pactl not found: sudo apt install pulseaudio-utils (PipeWire's pulse layer provides it)"; exit 1
fi
for name in fdb_to_bot fdb_from_bot; do
  if pactl list short sinks | grep -q "$name"; then
    echo "$name already exists"
  else
    pactl load-module module-null-sink sink_name=$name sink_properties=device.description=$name rate=48000 channels=1 >/dev/null
    echo "created $name"
  fi
done
echo
echo "Assistant settings:  microphone = fdb_to_bot.monitor   speaker = fdb_from_bot"
echo "Check with:          python -m fdbench devices"
