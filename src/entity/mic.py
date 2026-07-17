"""The real microphone: a 16 kHz mono float32 input stream, read in fixed-size frames.

Picking the RIGHT input device matters: Windows often defaults to a dead virtual mic (a VR
headset's silent input, "Sound Mapper", etc.), which hands back pure silence - so the Entity hears
nothing and just sits there. `choose_input_device` avoids that by taking the liveliest real input,
or an explicit override. Hardware I/O only; the segmentation/transcription it feeds is tested
without a mic.
"""

import math

import sounddevice as sd

SAMPLE_RATE = 16000
FRAME = 480  # 30 ms at 16 kHz


class Microphone:
    def __init__(self, *, device=None, samplerate=SAMPLE_RATE, blocksize=FRAME):
        self._blocksize = blocksize
        self._stream = sd.InputStream(
            device=device, samplerate=samplerate, channels=1, dtype="float32", blocksize=blocksize
        )
        self._stream.start()

    def read(self):
        data, _ = self._stream.read(self._blocksize)
        return data[:, 0].copy()

    def frames(self):
        while True:
            yield self.read()

    def close(self):
        self._stream.stop()
        self._stream.close()


def choose_input_device(devices, probe, *, override=None, hostapi=None):
    """Pick an input device index from a `sd.query_devices()` list.

    Only devices on `hostapi` are considered (when given) - on Windows the same mic is listed under
    several host APIs and some (WDM-KS) can't be opened for blocking reads, so we stick to the API
    the OS default uses. With `override` (a device-name substring), take the first such input whose
    name contains it. Otherwise probe each distinct input's live level via `probe(index) -> rms` and
    take the LIVELIEST - a real mic's self-noise always beats a disconnected virtual device's ~0, so
    this reliably avoids a dead default (a VR-headset mic) even in a silent room, where an
    absolute-threshold check would find nothing and fall back to that very dead default. Returns
    (index, name), or (None, None) only when there's no input device we could probe at all.
    """
    inputs = [
        (i, d)
        for i, d in enumerate(devices)
        if d.get("max_input_channels", 0) > 0 and (hostapi is None or d.get("hostapi") == hostapi)
    ]
    if override:
        want = override.strip().lower()
        for index, device in inputs:
            if want and want in device["name"].lower():
                return index, device["name"]
    best_index, best_name, best_level = None, None, None
    seen = set()
    for index, device in inputs:
        if device["name"] in seen:
            continue  # same physical mic, different host API - probe it once
        seen.add(device["name"])
        try:
            level = probe(index)
        except Exception:
            continue
        if level is None or not math.isfinite(level):
            continue
        if best_level is None or level > best_level:
            best_index, best_name, best_level = index, device["name"], level
    return best_index, best_name


def probe_input_device(index, *, seconds=0.4):
    """Measure a device's live RMS by briefly recording from it (real hardware; the pure selection
    logic in `choose_input_device` takes this as an injected callable). Values are clipped to the
    valid audio range first, so a glitchy device returning garbage can't overflow or masquerade as
    the loudest mic."""
    import numpy as np

    info = sd.query_devices(index)
    samplerate = int(info["default_samplerate"])
    recording = sd.rec(
        int(seconds * samplerate), samplerate=samplerate, channels=1, dtype="float32", device=index
    )
    sd.wait()
    clean = np.clip(np.nan_to_num(recording[:, 0]), -1.0, 1.0)
    return float(np.sqrt(np.mean(clean**2)))
