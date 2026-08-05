import threading

from excephalon.shutdown import consolidate


def test_consolidate_returns_the_facts_the_brain_reports():
    class Brain:
        def respond(self, prompt):
            return "- likes tea\n- moved to Berlin"

    assert consolidate(Brain(), timeout=2.0) == ["likes tea", "moved to Berlin"]


def test_consolidate_gives_up_when_the_brain_hangs_so_the_exit_stays_quick():
    still_running = threading.Event()

    class HangingBrain:
        def respond(self, prompt):
            still_running.wait(5.0)  # a consolidation that would otherwise stall the whole shutdown
            return "- too late to matter"

    facts = consolidate(HangingBrain(), timeout=0.05)

    assert facts == []  # timed out, saved nothing, and did not block the exit
    still_running.set()


def test_consolidate_swallows_a_brain_error():
    class Boom:
        def respond(self, prompt):
            raise RuntimeError("session already gone")

    assert consolidate(Boom(), timeout=1.0) == []


def test_consolidate_asks_with_the_consolidation_prompt():
    from excephalon.memory import CONSOLIDATION_PROMPT

    seen = []

    class Brain:
        def respond(self, prompt):
            seen.append(prompt)
            return "none"

    consolidate(Brain(), timeout=1.0)

    assert seen == [CONSOLIDATION_PROMPT]  # it asks the memory question, and "none" -> no facts


def test_the_process_leaves_without_letting_the_interpreter_tear_native_audio_down():
    # "Python quit unexpectedly" on every close. The crash report says it whole: main thread in
    # Py_Finalize unloading modules, thread 27 mid-call into native audio whose code pages were
    # already gone - daemon threads (the mic pump, playback) are BY DESIGN still alive at exit,
    # and interpreter finalization pulls the floor out from under them. Everything durable is
    # already on disk by the time the wind-down finishes (transcripts write per line, the fleet
    # record on the way down), so finalization buys nothing but that roulette: leave hard.
    from excephalon.shutdown import leave_process

    left = []
    leave_process(exit=left.append)
    assert left == [0]
