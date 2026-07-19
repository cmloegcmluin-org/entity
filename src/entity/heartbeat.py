"""Give the Entity a heartbeat, so it can speak up on its own instead of only when spoken to.

The brain is a request/response session: it hears an agent's check-in the moment it arrives, but it
only SAYS anything on the next turn the user prompts - so news that's hours old lands only when he
happens to ask. The monitor fixes that by asking the brain, on a quiet timer, "anything new from
your agents he doesn't know yet?" and pushing any real answer to the Outbox, which the conversation
loop then voices at the next lull. The poll uses remember=False so these silent checks don't fill
the conversation's recent-turns memory.

The poll only happens when agents are ACTUALLY running, and it names them. Asked the open question
"anything new from the agents you're running?" with nothing running at all, the brain went looking,
found inbox files left over from days before, and announced them to the user as fresh news in a
session where he had not yet said a word.
"""

import threading

HEARTBEAT_PROMPT = (
    "[System heartbeat - the user did NOT say this; it's an automatic check. Do NOT reply to him "
    "conversationally, do NOT start any work.] The agents running right now, and the ONLY ones you "
    "may report on, are: {agents}. Is there anything NEW from THOSE agents that he doesn't already "
    "know - one has a question for him, or finished, or got stuck? Judge only by what they have "
    "reported SINCE this session started; anything older he has already heard about. If yes, tell "
    "him in ONE short sentence, naming which agent. Otherwise reply with exactly one word and "
    "nothing else: nothing"
)


def _is_nothing(reply):
    return reply.strip().strip(".!").lower() == "nothing"


class HeartbeatMonitor:
    def __init__(self, brain, outbox, *, interval=120.0, prompt=HEARTBEAT_PROMPT, roster=None):
        self._brain = brain
        self._outbox = outbox
        self._interval = interval
        self._prompt = prompt
        self._roster = roster or (lambda: [])
        self._stop = threading.Event()

    def poll_once(self):
        """One heartbeat: ask the brain for new agent news, queue anything real. Swallows errors
        (a wedged/limited session, a barge-in cancel) - a background check must never crash the app."""
        agents = list(self._roster())
        if not agents:
            return  # nothing is running, so there is nothing it could truthfully have to report
        try:
            reply = self._brain.respond(self._prompt.format(agents=", ".join(agents)), remember=False)
        except Exception:
            return
        if reply and not _is_nothing(reply):
            self._outbox.push(reply)

    def run(self):
        while not self._stop.wait(self._interval):  # wait a beat between checks; exits promptly on stop
            self.poll_once()

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self._stop.set()
