from entity.mic import choose_input_device


def _dev(name, in_ch=2, sr=44100):
    return {"name": name, "max_input_channels": in_ch, "default_samplerate": sr}


def test_override_name_substring_wins_and_skips_probing():
    devices = [_dev("Microphone (Headset AirLink)"), _dev("Microphone (Onboard(R) Audio)")]
    probed = []

    idx, name = choose_input_device(devices, lambda i: probed.append(i) or 1.0, override="onboard")

    assert (idx, name) == (1, "Microphone (Onboard(R) Audio)")
    assert probed == []  # an explicit choice doesn't need to listen to anything


def test_the_liveliest_input_above_the_floor_is_chosen():
    devices = [_dev("Dead VR mic"), _dev("Real Mic", in_ch=1), _dev("Speakers", in_ch=0)]
    levels = {0: 0.00001, 1: 0.02}  # VR silent, real mic hears the room

    idx, name = choose_input_device(devices, lambda i: levels[i], floor=0.0005)

    assert (idx, name) == (1, "Real Mic")  # the silent default is passed over


def test_none_is_returned_when_every_device_is_silent():
    devices = [_dev("Dead A"), _dev("Dead B")]

    idx, name = choose_input_device(devices, lambda i: 0.00001, floor=0.0005)

    assert (idx, name) == (None, None)  # nothing clears the floor -> let the OS default stand


def test_a_device_that_fails_to_open_is_skipped():
    devices = [_dev("Broken"), _dev("Good", in_ch=1)]

    def probe(i):
        if i == 0:
            raise OSError("cannot open device")
        return 0.01

    idx, name = choose_input_device(devices, probe, floor=0.0005)

    assert (idx, name) == (1, "Good")


def test_each_physical_mic_is_probed_only_once():
    # Windows lists the same mic several times (one per host API); probing each is slow and noisy.
    devices = [_dev("Onboard", sr=44100), _dev("Onboard", sr=48000), _dev("Onboard", sr=16000)]
    probed = []

    choose_input_device(devices, lambda i: probed.append(i) or 0.01, floor=0.0005)

    assert probed == [0]


def test_output_only_devices_are_never_considered():
    devices = [_dev("Headphones", in_ch=0)]
    probed = []

    idx, name = choose_input_device(devices, lambda i: probed.append(i) or 1.0)

    assert (idx, name) == (None, None)
    assert probed == []


def test_only_devices_on_the_requested_host_api_are_considered():
    # Windows lists the same mic under several host APIs; some (WDM-KS) can't be opened for blocking
    # reads, so we stick to the OS default's API.
    devices = [
        {"name": "Onboard (WDM-KS)", "max_input_channels": 2, "default_samplerate": 44100, "hostapi": 3},
        {"name": "Onboard (MME)", "max_input_channels": 2, "default_samplerate": 44100, "hostapi": 0},
    ]
    probed = []

    idx, name = choose_input_device(devices, lambda i: probed.append(i) or 0.01, hostapi=0)

    assert (idx, name) == (1, "Onboard (MME)")
    assert probed == [1]  # the wrong-host-API entry isn't even opened


def test_a_device_returning_a_non_finite_level_is_ignored():
    devices = [_dev("Glitchy"), _dev("Good", in_ch=1)]

    def probe(i):
        return float("inf") if i == 0 else 0.01  # a garbage buffer can read as absurdly loud

    idx, name = choose_input_device(devices, probe, floor=0.0005)

    assert (idx, name) == (1, "Good")
