"""Where a piece of work stands between "described" and "landed" - tracked by code, not memory.

The describe -> deliver -> verify -> approve loop used to live entirely in the persona: the model
was ASKED to get see-it-running steps, ASKED to wait for a verdict, ASKED to wrap up afterwards.
On a good day it did all three. The stages here turn that order into a rule: a verdict cannot be
recorded for work that was never presented, work already approved and landing cannot be presented
again, and the steps the user needs are stored where every turn can read them rather than
remembered by whoever last spoke.
"""


class DeliveryError(ValueError):
    """A transition the loop does not allow - the message says what has to happen first."""


class Delivery:
    """One piece of work's place in the loop: building -> ready (presented, steps on file) ->
    landing (approved, being merged). A rejection sends ready back to building."""

    def __init__(self, stage="building", steps=None):
        self.stage = stage
        self.steps = steps

    def present(self, steps):
        if self.stage == "landing":
            raise DeliveryError("that work is already approved and landing - nothing to present")
        self.stage = "ready"
        self.steps = steps

    def verdict(self, approved):
        if self.stage != "ready":
            raise DeliveryError(
                "no verdict can be recorded - nothing has been presented for the user's eyes yet"
            )
        self.stage = "landing" if approved else "building"
        if not approved:
            self.steps = None  # rejected work returns with fresh steps, never yesterday's

    def describe(self):
        """The stage as a briefing phrase, or None while simply being built - the normal case
        earns no extra words."""
        if self.stage == "ready":
            return "presented, awaiting their verdict"
        if self.stage == "landing":
            return "approved, landing it"
        return None
