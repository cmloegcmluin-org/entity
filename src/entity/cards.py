"""The Config page's cards, editable from a terminal - the way in when the window isn't up.

The cards' one editor used to be the running app: its page, or its save endpoints. With the app
closed there was no lever at all - three Instructions rows the user had ordered deleted sat
waiting for a window to exist - and editing the files freehand risks drifting from the formats
the app's own savers keep. So the same savers get a command-line door:

    python -m entity.cards drop-instruction "leave the codebase cleaner" "end-to-end demo"
    python -m entity.cards tick 117

`drop-instruction` removes Instructions rows by unique fragment - a fragment matching no row, or
more than one, refuses the whole run rather than guessing. `tick` checks an Enhancements item off
by its number, the same flip the brain's own check_off_enhancement makes.

Run it while the window's Config page is not mid-edit: the page merges its own unsaved state on
its next save, and rows deleted underneath it can be carried back by that merge.
"""

import sys

from entity.memory import (
    DEFAULT_PERSONA_ADDITIONS_PATH,
    complete_enhancement_by_id,
    load_persona_additions,
    save_persona_additions,
)


def without_rows(text, fragments):
    """The bullet list minus the rows the fragments name - each fragment matching EXACTLY ONE
    row, or the whole edit is refused: deletion by pattern must never guess."""
    rows = [line for line in text.splitlines() if line.strip()]
    doomed = []
    for fragment in fragments:
        wanted = fragment.strip().lower()
        hits = [row for row in rows if wanted in row.lower()]
        if len(hits) != 1:
            raise SystemExit(f'"{fragment}" matches {len(hits)} rows - nothing was changed')
        doomed.extend(hits)
    return "\n".join(row for row in rows if row not in doomed)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["drop-instruction"] and len(argv) > 1:
        kept = without_rows(load_persona_additions(), argv[1:])
        save_persona_additions(kept, DEFAULT_PERSONA_ADDITIONS_PATH)
        print(f"dropped {len(argv) - 1}; the card now holds {len(kept.splitlines())} rows")
        return 0
    if argv[:1] == ["tick"] and len(argv) == 2:
        if complete_enhancement_by_id(int(argv[1])):
            print(f"#{argv[1]} checked off")
            return 0
        raise SystemExit(f"no open Enhancements item carries #{argv[1]} - nothing was changed")
    print("usage: python -m entity.cards drop-instruction <unique fragment>...\n"
          "       python -m entity.cards tick <enhancement number>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
