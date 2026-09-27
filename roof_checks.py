"""Fallwright — the checks table from the requirements. Not a compliance check.

Each check comes back as {"name", "status": pass | warn | fail, "message", "value"}. The
design fall rows follow BS 6229: design to 1:40 so the finished roof still makes 1:80
after deflection and build tolerance. With both falls at 1:40 the valleys come out at 1:57
and warn; that stays on so it is a decision, not a surprise."""

from roof_constants import (MIN_UPSTAND, MIN_DROP, MIN_SUMP_INS, MIN_FIRRING, MAX_FIRRING, DESIGN_FALL,
                            MIN_FALL, THRESHOLD_PASS, THRESHOLD_WARN, STD_SUMP_L, STD_SUMP_W, fall_label)
from roof_falls import region_of

_RANK = {"pass": 0, "warn": 1, "fail": 2}


def fall_status(ratio):
    if ratio is None:
        return "fail"
    if ratio <= DESIGN_FALL + 0.5:
        return "pass"
    return "warn" if ratio <= MIN_FALL + 0.5 else "fail"


def upstand_top(p, e):
    """Level top of an edge's membrane upstand above the structure: the upstand height
    over the highest finished level along an abutment; the wall top on a parapet, since the
    membrane is carried up and over it."""
    prof = e.get("profile") or []
    roof = [z for _t, z, s in prof if not s] or [z for _t, z, _s in prof]
    if not roof:
        return None
    if e["type"] == "parapet":
        rise = (e.get("wall") or {}).get("rise")
        return float(rise) if rise else max(roof) + p["upstand"]
    return max(roof) + p["upstand"]


def level_along(prof, t):
    """Finished level at t along an edge profile [(t, z, sunk)]; the higher at a step."""
    best = None
    for (t0, z0, _a), (t1, z1, _b) in zip(prof, prof[1:]):
        if t0 - 1e-6 <= t <= t1 + 1e-6:
            z = z0 if t1 - t0 < 1e-9 else z0 + (z1 - z0) * (t - t0) / (t1 - t0)
            best = z if best is None else max(best, z)
    return best


def check_roof(p, roof, falls):
    checks = []
    name = roof.get("name") or "Roof"

    def add(check, status, message, value=None):
        checks.append({"name": check, "status": status, "message": "%s: %s" % (name, message),
                       "value": value, "roof": name})

    facets, sumps, outlets = falls["facets"], falls["sumps"], falls["outlets"]
    edges = falls.get("edges") or []
    by_id = {e["id"]: e for e in edges}

    # Design falls: every facet, then every valley.
    worst, bad = "pass", []
    for f in facets:
        st = fall_status(f["fall"])
        if st != "pass":
            bad.append("%s %s" % (f["id"], fall_label(f["fall"])))
        worst = max(worst, st, key=_RANK.get)
    add("Design fall, facets", worst, "every facet at 1:%d or steeper" % DESIGN_FALL if not bad else
        "%s — flatter than the 1:%d design fall (1:%d is the least the finished roof may have)"
        % (", ".join(bad), DESIGN_FALL, MIN_FALL), len(bad))
    valleys = [c for c in falls.get("creases") or [] if c["kind"] == "valley"]
    if valleys:
        worst = max((fall_status(c["fall"]) for c in valleys), key=_RANK.get)
        falls_txt = sorted({c["label"] for c in valleys})
        if worst == "pass":
            add("Design fall, valleys", "pass", "%d valley(s), all at %s" % (len(valleys), ", ".join(falls_txt)))
        else:
            add("Design fall, valleys", worst, "%d valley(s) at %s: a valley falls at 1:sqrt(G² + Gc²), shallower "
                "than either fall. Steepen the cricket fall or the main fall to bring them to 1:%d"
                % (len(valleys), ", ".join(falls_txt), DESIGN_FALL), len(valleys))

    # Upstands, kerbs and door thresholds.
    for e in edges:
        if e["type"] not in ("abutment", "parapet") or not e.get("profile"):
            continue
        top = upstand_top(p, e)
        low = min(top - z for _t, z, sunk in e["profile"] if not sunk) if any(not s for _t, _z, s in e["profile"]) else None
        if low is None:
            continue
        st = "pass" if low >= MIN_UPSTAND - 0.5 else "fail"
        add("Upstand", st, "%s %s: upstand %.0f mm above the finished surface at its lowest%s"
            % (e["label"], e["id"], low, "" if st == "pass" else " — under %.0f mm" % MIN_UPSTAND), round(low, 1))
        for d in e.get("doors") or []:
            half = d["width"] / 2.0
            fin = max(z for z in (level_along(e["profile"], d["t"] - half), level_along(e["profile"], d["t"]),
                                  level_along(e["profile"], d["t"] + half)) if z is not None)
            margin = d["sill"] - fin
            st = "pass" if margin >= THRESHOLD_PASS - 0.5 else ("warn" if margin >= THRESHOLD_WARN - 0.5 else "fail")
            note = {"pass": "", "warn": " — needs an accessible threshold detail with drainage",
                    "fail": " — under %.0f mm: raise the sill or lower the roof here" % THRESHOLD_WARN}[st]
            add("Door threshold", st, "%s in %s: sill %.0f mm above the finished surface%s"
                % (d["name"], e["id"], margin, note), round(margin, 1))
    kerbs = sum(1 for e in edges if e["type"] == "kerb" and e["index"] == 0)
    if kerbs:
        st = "pass" if p["upstand"] >= MIN_UPSTAND - 0.5 else "fail"
        add("Kerb upstand", st, "%d kerb(s), upstand %.0f mm above the finished surface at the kerb's highest point"
            % (kerbs, p["upstand"]), p["upstand"])

    # Drainage.
    unreached = falls.get("unreached") or []
    if unreached:
        area = sum(pg.area for pg in unreached) / 1e6
        add("Drainage coverage", "fail", "%.2f m² that no outlet reaches — add an outlet or a gutter edge that "
            "faces it (hatched red on the plan)" % area, round(area, 3))
    elif facets:
        add("Drainage coverage", "pass", "every point faces a drain edge")
    ponding = falls.get("ponding") or []
    if ponding:
        add("Trapped water", "fail", "%s cannot drain: water traced down from it stops at a low point before "
            "reaching an outlet or gutter edge" % ", ".join(ponding), len(ponding))
    elif facets:
        add("Trapped water", "pass", "every facet traces down to an outlet or gutter edge")

    # Outlets.
    wanted = int(roof.get("n_outlets") if roof.get("n_outlets") is not None else len(outlets))
    placed = [o for o in outlets if "point" in o and not any("past the end" in x for x in o["errors"])]
    problems = [o for o in outlets if o["errors"] and not any("outside its sump" == x for x in o["errors"])]
    if wanted or outlets:
        st = "pass" if len(placed) >= wanted and not problems else "fail"
        msg = "%d of %d placed on the roof outline" % (len(placed), wanted)
        if problems:
            msg += "; " + "; ".join("outlet %d %s" % (o["n"], ", ".join(o["errors"])) for o in problems)
        add("Outlets placed", st, msg, len(placed))
    for o in outlets:
        if o["type"] != "hopper":
            continue
        et = o.get("edge_type")
        st = "pass" if et in ("abutment", "parapet") else "fail"
        add("Hopper position", st, "outlet %d is a hopper on %s (%s)%s" % (o["n"], o["edge"], et or "no edge",
            "" if st == "pass" else " — a hopper goes through a wall: abutment or parapet edges only"))
        if not o.get("sump"):
            add("Hopper sump", "fail", "outlet %d is a hopper with no sump — every hopper needs one" % o["n"])
        else:
            add("Hopper sump", "pass", "outlet %d has its sump" % o["n"])
    gutters = [s for s in sumps if s["kind"] == "gutter"]
    n_out = len([o for o in outlets if "point" in o])
    if n_out == 1 and not gutters:
        add("Overflow", "warn", "one outlet and no gutter edge: an overflow is required (place and detail it by hand)")
    elif n_out >= 2 or gutters:
        add("Overflow", "pass", "%s" % ("%d outlets" % n_out if n_out >= 2 else "a gutter edge takes water"))

    # Sumps.
    region = region_of(roof)
    holes = [h for h in region.interiors]
    for s in sumps:
        if s["kind"] != "sump":
            continue
        n = (s.get("outlet") or 0) + 1
        st = "pass" if s["drop"] >= MIN_DROP - 0.5 else "fail"
        add("Sump drop", st, "sump %d: %.0f mm from the floor's highest point to the rim%s"
            % (n, s["drop"], "" if st == "pass" else " — under %.0f mm" % MIN_DROP), round(s["drop"], 1))
        o = outlets[s["outlet"]]
        if any(x == "outlet is outside its sump" for x in o["errors"]):
            add("Outlet in sump", "fail", "outlet %d sits outside its sump's length" % n)
        e = by_id.get(s["edge"])
        clash = []
        if e and (s["b1"] < -0.5 or s["b2"] > float(e["length"]) + 0.5):
            clash.append("runs past the end of %s" % e["id"])
        for t in sumps:
            if t is not s and t["kind"] == "sump" and t["edge"] == s["edge"] and t["b1"] < s["b2"] - 0.5 and s["b1"] < t["b2"] - 0.5:
                clash.append("overlaps sump %d" % ((t.get("outlet") or 0) + 1))
        from shapely.geometry import Polygon
        for h in holes:
            if s["rect"].intersects(Polygon(h)) and s["rect"].intersection(Polygon(h)).area > 1.0:
                clash.append("overlaps a hole")
                break
        add("Sump fits", "fail" if clash else "pass", "sump %d %s" % (n, "; ".join(clash) if clash else
            "fits its edge, clear of other sumps and holes"))
        big = s["along"] or s["across"]
        if not big:
            add("Sump floor falls", "pass", "sump %d is standard (%.0f x %.0f): level floor" % (n, s["b2"] - s["b1"], s["W"]))
        else:
            st = fall_status(p["sump_fall"])
            add("Sump floor falls", st, "sump %d is larger than %.0f x %.0f: floor falls at %s%s"
                % (n, STD_SUMP_L, STD_SUMP_W, fall_label(p["sump_fall"]),
                   "" if st == "pass" else " — steepen it to 1:%d" % DESIGN_FALL), p["sump_fall"])
    if any(s["kind"] == "sump" for s in sumps):
        st = "pass" if p["sump_ins"] >= MIN_SUMP_INS - 0.5 else "fail"
        add("Sump insulation", st, "%.0f mm under the sumps%s" % (p["sump_ins"], "" if st == "pass" else
            " — under the %.0f mm minimum" % MIN_SUMP_INS), p["sump_ins"])

    # Firrings.
    if falls.get("max_depth") is not None:
        mx, mn = falls["max_depth"], falls["min_depth"]
        add("Maximum firring depth", "pass" if mx <= MAX_FIRRING + 0.5 else "warn",
            "%.0f mm%s" % (mx, "" if mx <= MAX_FIRRING + 0.5 else " — over %.0f mm: deep firrings need restraint, "
                           "and the roof edge gets tall" % MAX_FIRRING), round(mx, 1))
        add("Minimum firring depth", "pass" if mn >= MIN_FIRRING - 0.05 else "fail",
            "%.0f mm%s" % (mn, "" if mn >= MIN_FIRRING - 0.05 else " — under the %.0f mm minimum" % MIN_FIRRING),
            round(mn, 1))
    merged = falls.get("merged") or 0
    small = [f["id"] for f in facets if f.get("small")]
    if merged or small:
        add("Facet size", "warn", "%s under 0.1 m²%s" % ("%d facet(s)" % merged if merged else ", ".join(small),
            " merged into a neighbour" if merged else " with no neighbour to merge into"), merged or len(small))
    elif facets:
        add("Facet size", "pass", "every facet at least 0.1 m²")
    return checks
