"""How much of the plan the last five hours have burned, and the warning he asked for.

"warn Douglas when he is low on credits." There is no meter to read the plan's true remaining
balance from, but every Claude session on this machine writes its token usage into the local
records under ~/.claude/projects - so what CAN be known is how many tokens the last five hours
(the shape of the plan's usage window) have consumed, summed across everything: Entity's own
brain, its agents, and any terminal sessions. That estimate is compared against a line HE sets
- runtime/usage-budget.txt, a single number of tokens - and Entity says something when the
spending crosses 80% of it, and again at the line itself. No file, no line, no warnings: a
guessed default would fire wrong in both directions.
"""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIVE_HOURS = timedelta(hours=5)

# The token kinds that spend the plan. Cache reads are billed near-free and would swamp the sum
# with numbers that cost nothing, so they are left out of the estimate.
_SPENT = ("input_tokens", "output_tokens", "cache_creation_input_tokens")

DEFAULT_RECORDS = Path.home() / ".claude" / "projects"


def _within_window(stamp, horizon):
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")) >= horizon
    except (ValueError, AttributeError):
        return False


def block_tokens(records=DEFAULT_RECORDS, *, now=None):
    """Tokens spent in the last five hours, summed from the machine's local session records.

    An estimate: it counts what the records say was sent and produced, which tracks the plan's
    own accounting closely enough to warn from, and nothing finer. Files untouched for five
    hours cannot hold anything in the window, so they are skipped unread."""
    now = now or datetime.now(timezone.utc)
    horizon = now - FIVE_HOURS
    total = 0
    if not Path(records).exists():
        return 0
    for path in Path(records).glob("**/*.jsonl"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < horizon:
                continue
            with open(path, encoding="utf-8", errors="replace") as lines:
                for line in lines:
                    if '"usage"' not in line:
                        continue  # the cheap gate; parsing every line would cost real seconds
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if not _within_window(entry.get("timestamp"), horizon):
                        continue
                    usage = (entry.get("message") or {}).get("usage") or {}
                    total += sum(int(usage.get(kind) or 0) for kind in _SPENT)
        except OSError:
            continue  # a record that cannot be read is spending that cannot be counted
    return total


def budget_line(path):
    """The warning line he set, in tokens - or None when he has not set one."""
    try:
        return int(Path(path).read_text(encoding="utf-8").strip().replace(",", "").replace("_", ""))
    except (OSError, ValueError):
        return None


def save_budget(path, tokens):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(f"{int(tokens)}\n", encoding="utf-8")


class UsageWatch:
    """Says something when the five-hour spending crosses his line - once per crossing.

    80% is the warning that leaves room to wind down; the line itself is the second and last.
    Each level speaks once: a watch that repeated itself every poll would be the nagging he had
    every stock phrase deleted over. Spending falling back under half the line re-arms both,
    because that is a new block of work, not the same crossing twice."""

    def __init__(self, outbox, budget_path, *, measure=block_tokens):
        self._outbox = outbox
        self._budget_path = budget_path
        self._measure = measure
        self._warned = 0.0

    def poll_once(self):
        line = budget_line(self._budget_path)
        if line is None or line <= 0:
            return
        tokens = self._measure()
        if tokens < line / 2:
            self._warned = 0.0
        level = 1.0 if tokens >= line else 0.8 if tokens >= 0.8 * line else 0.0
        if level > self._warned:
            self._warned = level
            share = round(100 * tokens / line)
            self._outbox.push(
                f"About your credits: roughly {tokens:,} tokens have gone out in the last five "
                f"hours - {share}% of the {line:,} you set as your warning line."
            )

    def run(self, *, stop, every=300, sleep=time.sleep):
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass  # a broken estimate must never take the session down with it
            sleep(every)
