"""Find the two virtual audio cables and refuse real microphones/speakers unless forced."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

VIRTUAL_PATTERNS = [
    r"cable",            # VB-Audio CABLE Input/Output, CABLE-A, CABLE-B, Hi-Fi Cable
    r"vb-audio", r"voicemeeter",
    r"fdb_", r"fdbench", r"null", r"virtual", r"loopback", r"snd_aloop", r"aloop", r"monitor of",
    r"blackhole", r"soundflower",
]
PHYSICAL_HINTS = [r"realtek", r"microphone", r"mic\b", r"speaker", r"headphone", r"headset", r"usb audio", r"bcm2835",
                  r"hdmi", r"jabra", r"anker", r"webcam", r"camera", r"conexant", r"intel"]


@dataclass
class Device:
    index: int
    name: str
    max_in: int
    max_out: int
    default_sr: float
    hostapi: str

    @property
    def is_virtual(self) -> bool:
        n = self.name.lower()
        return any(re.search(p, n) for p in VIRTUAL_PATTERNS)

    @property
    def looks_physical(self) -> bool:
        n = self.name.lower()
        return any(re.search(p, n) for p in PHYSICAL_HINTS) and not self.is_virtual

    def short(self) -> str:
        return f"[{self.index}] {self.name} ({self.hostapi}, in {self.max_in}/out {self.max_out}, {int(self.default_sr)} Hz)"


def list_devices() -> List[Device]:
    import sounddevice as sd

    apis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        out.append(Device(i, d["name"], d["max_input_channels"], d["max_output_channels"],
                          d["default_samplerate"], apis[d["hostapi"]]["name"]))
    return out


def _prefer_hostapi(devs: List[Device]) -> List[Device]:
    """On Windows prefer WASAPI entries (lower latency, exact names); MME truncates names."""
    order = {"Windows WASAPI": 0, "Windows WDM-KS": 1, "Windows DirectSound": 2, "MME": 3}
    return sorted(devs, key=lambda d: order.get(d.hostapi, 0))


def candidates(kind: str) -> List[Device]:
    """kind: 'to_bot' (our output = the assistant's mic) or 'from_bot' (our input = the assistant's speaker)."""
    devs = list_devices()
    if kind == "to_bot":
        pool = [d for d in devs if d.max_out > 0 and d.is_virtual]
    else:
        pool = [d for d in devs if d.max_in > 0 and d.is_virtual]
    return _prefer_hostapi(pool)


def find_by_name(fragment: str, kind: str) -> Optional[Device]:
    frag = fragment.lower()
    for d in _prefer_hostapi(list_devices()):
        if frag in d.name.lower() and ((kind == "to_bot" and d.max_out > 0) or (kind == "from_bot" and d.max_in > 0)):
            return d
    return None


def auto_pick() -> dict:
    """Best guess of the two cables. Returns {'to_bot': Device|None, 'from_bot': Device|None, 'ambiguous': bool}."""
    to_bot = candidates("to_bot")
    from_bot = candidates("from_bot")
    pick_to = to_bot[0] if to_bot else None
    pick_from = None
    for d in from_bot:
        # avoid using the same cable for both directions (VB-CABLE: "CABLE Input" is the output end,
        # "CABLE Output" is the input end of the SAME cable)
        if pick_to and _same_cable(pick_to.name, d.name):
            continue
        pick_from = d
        break
    if pick_from is None and from_bot:
        pick_from = from_bot[0]
    ambiguous = len(to_bot) > 1 or len(from_bot) > 1
    return {"to_bot": pick_to, "from_bot": pick_from, "ambiguous": ambiguous,
            "to_bot_options": to_bot, "from_bot_options": from_bot}


def _same_cable(a: str, b: str) -> bool:
    def key(n):
        n = n.lower()
        n = re.sub(r"\b(input|output|in|out)\b", "", n)
        n = re.sub(r"\(.*?\)", "", n)
        return re.sub(r"\s+", " ", n).strip()
    return key(a) == key(b)
