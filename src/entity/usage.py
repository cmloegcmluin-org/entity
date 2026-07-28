"""The credit warning: how much of the week the plan has burned, spoken at the moments he chose.

"I only care about my weekly limit. I just want Excephalon to say something aloud to me when I
hit 50% of my weekly limit, also 80%, 90%, 95%, 98%, and 99%." There is no meter on this machine
that reads Anthropic's true weekly balance, but every Claude session here writes its token usage
into the local records under ~/.claude/projects - so the rolling seven days are summed from
those and compared against the one number he provides: runtime/usage-weekly-limit.txt, his
weekly line in tokens. No file, no warnings - a guessed limit would fire wrong in both
directions. Each threshold speaks exactly once per week of spending; the count re-arms when the
rolling week falls back under 40% (a new week of work, not the same crossing twice).
"""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

WEEK = timedelta(days=7)

# The moments he asked to hear about, in his own numbers.
THRESHOLDS = (0.50, 0.80, 0.90, 0.95, 0.98, 0.99)

# The token kinds that spend the plan. Cache reads are billed near-free and would swamp the sum
# with numbers that cost nothing, so they are left out of the estimate.
_SPENT = ("input_tokens", "output_tokens", "cache_creation_input_tokens")

DEFAULT_RECORDS = Path.home() / ".claude" / "projects"


def _within_window(stamp, horizon):
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")) >= horizon
    except (ValueError, AttributeError):
        return False


def week_tokens(records=DEFAULT_RECORDS, *, now=None):
    """Tokens spent in the rolling last seven days, summed from the machine's local session
    records - an estimate that tracks the plan's own accounting closely enough to warn from.
    Files untouched for seven days cannot hold anything in the window, so they are skipped
    unread."""
    now = now or datetime.now(timezone.utc)
    horizon = now - WEEK
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


def weekly_limit(path):
    """His weekly line, in tokens - or None when he has not set one."""
    try:
        return int(Path(path).read_text(encoding="utf-8").strip().replace(",", "").replace("_", ""))
    except (OSError, ValueError):
        return None


def save_weekly_limit(path, tokens):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(f"{int(tokens)}\n", encoding="utf-8")


class UsageWatch:
    """Speaks once at each of his chosen shares of the weekly line - 50, 80, 90, 95, 98, 99
    percent - and never twice for the same crossing: a watch that repeated itself every poll
    would be the nagging he had every stock phrase deleted over. The rolling week falling back
    under 40% re-arms the whole ladder."""

    def __init__(self, outbox, limit_path, *, measure=week_tokens):
        self._outbox = outbox
        self._limit_path = limit_path
        self._measure = measure
        self._warned = 0.0

    def poll_once(self):
        line = weekly_limit(self._limit_path)
        if line is None or line <= 0:
            return
        tokens = self._measure()
        if tokens < 0.4 * line:
            self._warned = 0.0
        crossed = max((t for t in THRESHOLDS if tokens >= t * line), default=0.0)
        if crossed > self._warned:
            self._warned = crossed
            share = round(100 * tokens / line)
            self._outbox.push(
                f"About your credits: the last seven days have used roughly {tokens:,} tokens - "
                f"{share}% of your weekly line of {line:,}."
            )

    def run(self, *, stop, every=600, sleep=time.sleep):
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass  # a broken estimate must never take the session down with it
            sleep(every)
