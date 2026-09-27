"""Replay a saved roof extraction outside the browser.

    python tests/replay.py fallwright-payload-Roof-1-....json [--budget SECONDS]

In the page, fallwright.payload() downloads the last extraction payload. This runs
extract_roof on it in ordinary Python with debug on, so every stage and every nearby element
is printed as it goes with its time, then prints the result, the stage times and the
slowest elements. A roof that is slow or stuck in the browser can be profiled here.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from roof_extract import extract_roof  # noqa: E402


def replay(path, budget=None):
    with open(path) as f:
        payload = json.load(f)
    options = payload.setdefault("options", {})
    options["debug"] = True
    if budget is not None:
        options["budget_s"] = budget
    result = extract_roof(payload)
    print("\nresult: %s ok=%s, %d edges, %d hole(s)" % (
        result.get("name"), result.get("ok"), len(result.get("edges") or []), len(result.get("holes") or [])))
    for w in result.get("warnings") or []:
        print("  warning:", w)
    timing = result.get("timing") or {}
    print("stages (ms):")
    for stage, ms in (timing.get("stages") or {}).items():
        print("  %-14s %8.1f" % (stage, ms))
    print("slowest elements:")
    for t in timing.get("slowest") or []:
        print("  %-13s %d/%d %s %r, %d tris, %.1f ms" % (t["stage"], t["index"], t["total"], t["type"], t["name"],
                                                          t["tris"], t["ms"]))
    return result


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    budget = None
    if "--budget" in argv:
        budget = float(argv[argv.index("--budget") + 1])
    result = replay(argv[0], budget)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
