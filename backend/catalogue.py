"""Standard appliance / furniture sizes (mm), shared by the viewer and the AI planner.

w = across the front, d = front to back, clear = working / door clearance needed in front.
"""

CATALOGUE = [
    {"kind": "fridge", "name": "Under-counter fridge", "w": 600, "d": 600, "clear": 800},
    {"kind": "fridge", "name": "Tall fridge-freezer", "w": 700, "d": 700, "clear": 900},
    {"kind": "fridge", "name": "American fridge-freezer", "w": 910, "d": 720, "clear": 1000},
    {"kind": "cooker", "name": "Cooker", "w": 600, "d": 600, "clear": 1000},
    {"kind": "cooker", "name": "Range cooker", "w": 900, "d": 600, "clear": 1000},
    {"kind": "dishwasher", "name": "Dishwasher", "w": 600, "d": 600, "clear": 800},
    {"kind": "washer", "name": "Washing machine", "w": 600, "d": 600, "clear": 800},
    {"kind": "washer", "name": "Washer + dryer stack", "w": 600, "d": 650, "clear": 800},
    {"kind": "cylinder", "name": "Hot water cylinder", "w": 600, "d": 600, "clear": 600},
    {"kind": "island", "name": "Kitchen island", "w": 2400, "d": 1000, "clear": 1000},
    {"kind": "table", "name": "Dining table (6)", "w": 1800, "d": 900, "clear": 0},
    {"kind": "sofa", "name": "Sofa (3-seat)", "w": 2100, "d": 900, "clear": 600},
    {"kind": "sofa", "name": "Sofa (2-seat)", "w": 1700, "d": 900, "clear": 600},
    {"kind": "tv", "name": "TV unit", "w": 1600, "d": 450, "clear": 600},
    {"kind": "bed", "name": "Double bed", "w": 1500, "d": 2000, "clear": 600},
    {"kind": "wardrobe", "name": "Wardrobe", "w": 1000, "d": 600, "clear": 800},
]


# What a bare word like "fridge" means when several entries share a kind.
DEFAULT_BY_KIND = {"fridge": "Tall fridge-freezer", "cooker": "Cooker", "washer": "Washing machine", "sofa": "Sofa (3-seat)"}


def default_for_kind(kind: str) -> str | None:
    return DEFAULT_BY_KIND.get(kind)


def match_catalogue(query: str, extra: list[dict] | None = None) -> dict | None:
    """Find the best catalogue entry for free text ("the fridge", "range cooker")."""
    q = " ".join(query.lower().replace("-", " ").split())
    if not q:
        return None
    entries = list(extra or []) + CATALOGUE   # user presets win ties

    def norm(s: str) -> str:
        return " ".join(s.lower().replace("-", " ").replace("(", " ").replace(")", " ").replace("+", " ").split())

    best, best_score = None, 0
    for e in entries:
        name, kind = norm(e["name"]), e["kind"].lower()
        words = set(q.split())
        score = 0
        if q == name:
            score = 100
        elif name.startswith(q) or q.startswith(name):
            score = 80
        elif q in name or name in q:
            score = 70
        elif kind == q or kind in words:
            score = 50
        else:
            overlap = len(words & set(name.split()))
            score = 20 * overlap
        if score == 50 and DEFAULT_BY_KIND.get(kind) == e["name"]:
            score = 55      # a bare kind word means the standard model of that kind
        if score > best_score:
            best, best_score = e, score
    return best if best_score >= 20 else None
