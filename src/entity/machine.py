"""Which desk this is.

Excephalon was written for one Windows desk and now also runs on a Mac when its user is on the
road. A handful of things genuinely differ between the two - what opens a folder, which robot
voice serves, how a child process is kept from conjuring a console window, how the app relaunches
itself - and each of those stays with the thing it belongs to. What lives here is the QUESTION,
asked once, so a desk gained or lost is one file to read rather than a grep for `sys.platform`
across the app.

Anything that can be written to work the same on both is: this is for the differences that are
real, not for branching on habit.
"""

import sys

WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"
