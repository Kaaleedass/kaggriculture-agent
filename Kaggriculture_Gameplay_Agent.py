
"""
Kaggriculture agent — hybrid crop + livestock economic planner.

Objective: maximize FINAL cash at turn 720 (day 30). Unsold inventory scores zero.

Why livestock (learned from the $102k winner: 9 cows, 4 sheep, 240 fertilizer,
226 milk, 132 wool sold):
* Every surviving animal produces 1 FERTILIZER per day regardless of feeding, and
  fertilizer's price decays gently (linear curve), so a herd is a large, steady cash
  stream that doesn't crash like premium crops. A single fed cow nets ~$9k over a
  season in local tests.
* Milk/wool/eggs add product revenue on top (they glut with a big herd, so they're
  the bonus, not the core).

Hard mechanics this agent respects (verified against the environment source):
* Inventory is wiped to the shed every night; units respawn at the shed each morning
  with empty inventory. So feeding an animal is a DAILY logistics loop: fetch wheat
  from the shed -> carry to the animal -> FEED. Animals escape (unrecoverable) after
  2 unfed days, so feeding must be reliable.
* Animals are clustered near the shed to minimize the daily wheat-fetch travel.
* Crops occupy the outer tiles; the lane system keeps each unit on a compact strip.

Architecture:
  - Livestock manager: buys/places animals near shed, assigns feeder units that fetch
    wheat and service (FEED, HARVEST, COLLECT_FERTILIZER, CARE) each day.
  - Crop manager: melon (capped, high value) + strawberry + continuous wheat, lane
    assigned, horizon-aware (nothing planted that can't mature by day 30).
  - Capital: keeps a cash buffer; expands land when solvent; buys wheat to feed the
    herd; sells products + fertilizer steadily and liquidates everything at the end.
"""

# ---- Environment constants (from kaggriculture.py) ----
CROP = {
    "WHEAT":      {"seed": 10, "first": 2,  "maxday": 4,  "ongoing": False, "base": 25,  "grow": 4},
    "CARROT":     {"seed": 20, "first": 2,  "maxday": 3,  "ongoing": False, "base": 35,  "grow": 3},
    "TOMATO":     {"seed": 50, "first": 8,  "maxday": 8,  "ongoing": True,  "base": 60,  "grow": 8},
    "STRAWBERRY": {"seed": 100,"first": 10, "maxday": 10, "ongoing": True,  "base": 120, "grow": 10},
    "MELON":      {"seed": 80, "first": 10, "maxday": 12, "ongoing": False, "base": 250, "grow": 10},
}
ANIMAL = {
    "GOOSE": {"cost": 300, "structure": "COOP",    "first": 4, "interval": 1, "max_held": 4, "product": "EGG",  "pbase": 50},
    "COW":   {"cost": 400, "structure": "PASTURE", "first": 8, "interval": 2, "max_held": 6, "product": "MILK", "pbase": 160},
    "SHEEP": {"cost": 500, "structure": "PASTURE", "first": 6, "interval": 3, "max_held": 6, "product": "WOOL", "pbase": 200},
}
LAND_ORDER = ["NE", "SW", "SE"]
LAND_PRICE = {"NE": 1000, "SW": 2000, "SE": 4000}
FIB = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377]
LAST_DAY = 29

# Strategy knobs (tuned empirically below).
MELON_CAP = 18          # standing melon plants
STRAW_CAP = 9           # premium ongoing crop, own market + strong town demand
TARGET_ANIMALS = 13     # herd size cap (matches winner)
CREW_CAP = 9            # more crew costs more in hire fees than it returns


def _agent_impl(obs):
    try:
        return _decide(obs)
    except Exception:
        return {"farmer": ["PASS"], "hands": [], "market": []}


def _g(obj, key, default=None):
    v = default
    if isinstance(obj, dict):
        v = obj.get(key, default)
    else:
        try:
            v = getattr(obj, key)
        except AttributeError:
            try:
                v = obj[key]
            except Exception:
                v = default
    return default if v is None else v


def _num(v, default=0):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, dict):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _decide(obs):
    player = _num(_g(obs, "player", 0), 0)
    farms = _g(obs, "farms", [])
    me = farms[player]
    priv = _g(obs, "private", {})
    day = _num(_g(obs, "day", 0), 0)
    hour = _num(_g(obs, "hour", 0), 0)
    tiles = _g(me, "tiles", [])
    money = float(_num(_g(me, "money", 0), 0))
    shed = {k: _num(v, 0) for k, v in (_g(priv, "shed", {}) or {}).items()}
    seeds = {k: _num(v, 0) for k, v in (_g(priv, "seeds", {}) or {}).items()}
    inv_raw = _g(priv, "inventories", [{}]) or [{}]
    inventories = [{k: _num(v, 0) for k, v in dict(iv).items()} if iv else {} for iv in inv_raw]
    unlocked = set(_g(me, "unlocked_quadrants", ["NW"]) or ["NW"])
    hires_today = _num(_g(me, "hires_today", 0), 0)
    market_obs = _g(obs, "market", {})
    prices = {k: _num(v, 0) for k, v in (_g(market_obs, "prices", {}) or {}).items()}

    N = len(tiles)
    half = N // 2
    turns_left_today = 24 - hour

    def quad(x, y):
        return ("N" if y < half else "S") + ("W" if x < half else "E")

    def owned(x, y):
        return quad(x, y) in unlocked

    def T(x, y):
        return tiles[y][x]

    def is_plant(t):
        return isinstance(t, dict) and t.get("kind") == "PLANT"

    def is_animal(t):
        return isinstance(t, dict) and "animal" in t and t.get("animal")

    def is_empty_struct(t):
        return isinstance(t, dict) and t.get("kind") in ("COOP", "PASTURE") and not t.get("animal")

    def is_weed(t):
        return isinstance(t, dict) and t.get("kind") == "WEED"

    shed_tiles = [(half - 1, half - 1), (half, half - 1), (half - 1, half), (half, half)]
    shed_set = set(shed_tiles)

    def moveto(fx, fy, tx, ty):
        dx, dy = tx - fx, ty - fy
        if abs(dx) >= abs(dy):
            if dx > 0:
                return ["EAST"]
            if dx < 0:
                return ["WEST"]
        if dy > 0:
            return ["SOUTH"]
        if dy < 0:
            return ["NORTH"]
        return ["PASS"]

    def nearest_shed_tile(fx, fy):
        best, bd = shed_tiles[0], 10 ** 9
        for s in shed_tiles:
            d = abs(s[0] - fx) + abs(s[1] - fy)
            if d < bd:
                bd, best = d, s
        return best

    # ---- crop helpers ----
    def plant_ready(t):
        if not is_plant(t):
            return False
        if _num(t.get("yield_units", 0), 0) <= 0:
            return False
        cd = CROP[t["crop"]]
        return (day - _num(t.get("planted_day", 0), 0)) >= cd["first"]

    def needs_water(t):
        return is_plant(t) and not t.get("watered_today", False)

    def water_adds_value(t):
        if not is_plant(t):
            return False
        cd = CROP[t["crop"]]
        age = day - _num(t.get("planted_day", 0), 0)
        if cd["ongoing"]:
            return True
        wstart = (cd["maxday"] + 1) // 2
        if wstart <= age <= cd["maxday"]:
            return True
        return age < cd["first"]

    def best_crop_to_plant():
        if day <= LAST_DAY - CROP["MELON"]["grow"]:
            order = ["MELON", "STRAWBERRY", "WHEAT", "CARROT", "TOMATO"]
        else:
            order = []
            if day <= LAST_DAY - CROP["CARROT"]["grow"]:
                order.append("CARROT")
            if day <= LAST_DAY - CROP["WHEAT"]["grow"]:
                order.append("WHEAT")
        for c in order:
            if seeds_left.get(c, 0) > 0:
                return c
        for c in ("WHEAT", "CARROT"):
            if seeds_left.get(c, 0) > 0 and day <= LAST_DAY - CROP[c]["grow"]:
                return c
        return None

    # ---- scan the field ----
    animals = []          # (x,y,tile)
    empty_structs = []    # (x,y,tile) pasture/coop awaiting an animal
    empty_tiles = []      # owned empty
    weed_tiles = []
    water_list = []
    harvest_list = []
    plants_now = 0
    melon_now = 0
    straw_now = 0
    empty_owned = 0
    for y in range(N):
        for x in range(N):
            t = T(x, y)
            if is_animal(t):
                animals.append((x, y, t))
            elif is_empty_struct(t):
                empty_structs.append((x, y, t))
            elif is_plant(t):
                plants_now += 1
                if t["crop"] == "MELON":
                    melon_now += 1
                elif t["crop"] == "STRAWBERRY":
                    straw_now += 1
                if needs_water(t) and water_adds_value(t):
                    water_list.append((x, y))
                if plant_ready(t):
                    harvest_list.append((x, y))
            elif is_weed(t) and owned(x, y):
                weed_tiles.append((x, y))
            elif t is None and owned(x, y):
                empty_tiles.append((x, y))
                empty_owned += 1

    n_animals = len(animals)

    # Animals that still need service today, split by need.
    feed_needed = [(x, y) for (x, y, t) in animals if not t.get("fed_today", False)]
    animal_harvest = [(x, y) for (x, y, t) in animals if _num(t.get("yield_units", 0), 0) > 0]
    fert_ready = [(x, y) for (x, y, t) in animals if t.get("fertilizer_available", False)]
    care_needed = [(x, y) for (x, y, t) in animals if not t.get("cared_today", False) and t.get("fed_today", False)]

    # ================= MARKET =================
    market = []

    # 1) Sell products + fertilizer. Dump glut-resistant + fertilizer; drip premium.
    endgame = day >= LAST_DAY - 1
    shed_total = sum(v for k, v in shed.items() if k not in ("GOOSE", "COW", "SHEEP"))
    pressure = shed_total >= 75
    for item, qty in shed.items():
        if qty <= 0 or item in ("GOOSE", "COW", "SHEEP"):
            continue
        if item == "WHEAT":
            # keep a feed reserve for the herd; sell the surplus
            reserve = min(40, n_animals * 2 + 6)
            if qty > reserve:
                market.append(["SELL", "WHEAT", qty - reserve])
            continue
        if endgame or pressure:
            market.append(["SELL", item, qty])
            continue
        if item in ("CARROT", "TOMATO", "EGG", "FERTILIZER"):
            market.append(["SELL", item, qty])
        else:
            pr = prices.get(item, 0)
            base = ANIMAL.get(item, {}).get("pbase") or CROP.get(item, {}).get("base", 100)
            if item == "MELON":
                chunk = 3 if pr >= 150 else (2 if pr >= 60 else 1)
            else:
                chunk = 2 if pr >= base * 0.5 else 1
            market.append(["SELL", item, min(qty, chunk)])

    # 2) Keep wheat stocked for feeding the herd (buy a little when low).
    wheat_reserve_target = n_animals * 2 + 4
    if n_animals > 0 and shed.get("WHEAT", 0) + sum(iv.get("WHEAT", 0) for iv in inventories) < wheat_reserve_target and money > 200:
        buy = min(6, wheat_reserve_target)
        market.append(["BUY_PRODUCT", "WHEAT", buy])

    # 3) Hire crew (throughput). Size to workload: crops + animal servicing.
    crop_work = plants_now + len(harvest_list) + len(weed_tiles) + min(empty_owned, sum(seeds.values()))
    animal_work = 3 * n_animals  # feed+harvest+collect roughly
    workload = crop_work + animal_work
    target_crew = min(CREW_CAP, max(0, (workload + 6) // 7))
    if empty_owned > 0 and sum(seeds.values()) > 0:
        target_crew = max(target_crew, 3)
    if n_animals > 0:
        target_crew = max(target_crew, min(CREW_CAP, 2 + n_animals // 2))
    if hires_today < target_crew:
        nc = FIB[min(hires_today, len(FIB) - 1)]
        if money >= nc + 40:
            market.append(["HIRE"])

    # 4) Buy animals: convert surplus cash into the herd once a crop base exists and
    #    there's enough season to recoup. Buy at most ONE per turn and keep a cash
    #    cushion so we never drain to near-zero (which starves feeding + crops).
    #    Cows are the workhorse (milk + fert); geese add cheap fertilizer volume.
    animals_incoming = sum(shed.get(an, 0) for an in ANIMAL)  # bought, waiting in shed
    animals_carried = sum(iv.get(an, 0) for iv in inventories for an in ANIMAL)  # mid-placement
    effective_herd = n_animals + animals_incoming + animals_carried
    can_place = len(empty_structs) + empty_owned
    # Feeders can only keep so many animals alive; don't buy past what the crew can feed.
    max_feedable = 3 * min(CREW_CAP, max(hires_today, 3))  # ~3 animals serviceable per unit
    herd_cap = min(TARGET_ANIMALS, max_feedable)
    if (day <= 17 and effective_herd < herd_cap and plants_now >= 6
            and can_place > 0 and animals_incoming == 0 and animals_carried == 0):
        want = "COW" if (day <= 13 and money >= 1500) else "GOOSE"
        a = ANIMAL[want]
        cushion = 500 + effective_herd * 20
        if money >= a["cost"] + cushion:
            market.append(["BUY_ANIMAL", want, 1])

    # 5) Land expansion — expand to grow the farm, but weeds on unworked tiles waste
    #    the investment, so keep expansion roughly matched to crew capacity. Allow up to
    #    3 quadrants when the crew + herd justify it.
    owned_tiles = sum(1 for y in range(N) for x in range(N) if owned(x, y))
    if money >= 1200:
        if "NE" not in unlocked and money >= LAND_PRICE["NE"] + 500 and day <= 20:
            market.append(["BUY_LAND"])
        elif "NE" in unlocked and "SW" not in unlocked and money >= LAND_PRICE["SW"] + 1200 and day <= 18:
            market.append(["BUY_LAND"])
        elif ("SW" in unlocked and "SE" not in unlocked and money >= LAND_PRICE["SE"] + 4000
              and day <= 14 and empty_owned <= 8):
            market.append(["BUY_LAND"])

    # 6) Seeds (capacity-limited, just-in-time so cash isn't frozen in a seed pile).
    market += _seed_plan(day, money, tiles, N, owned, is_plant, seeds, melon_now,
                         straw_now, plants_now, empty_owned, n_animals, hires_today)

    market = market[:10]

    # ================= UNIT TASKS =================
    units = []
    fpos = tuple(_g(me, "farmer", [half - 1, half - 1]))
    units.append((fpos, inventories[0] if inventories else {}))
    for i, hp in enumerate(_g(me, "hands", []) or []):
        iv = inventories[i + 1] if i + 1 < len(inventories) else {}
        units.append((tuple(hp), iv))
    n_units = len(units)

    seeds_left = dict(seeds)
    claimed = set()          # claimed tiles (crop/weed/plant)
    claimed_animals = set()  # claimed animal tiles

    # Decide how many units are "feeders" (dedicated to the herd) vs croppers.
    # Enough feeders to service the herd: ~1 feeder per 3 animals, min 1 if herd exists.
    n_feeders = 0
    if n_animals > 0:
        n_feeders = min(n_units, max(1, (n_animals + 2) // 3))

    # Lane columns for croppers (units n_feeders..).
    owned_cols = [x for x in range(N) if any(owned(x, y) for y in range(N))]

    def crop_lane(ci, total_croppers):
        if not owned_cols or total_croppers <= 0:
            return set(owned_cols)
        start = (ci * len(owned_cols)) // total_croppers
        end = ((ci + 1) * len(owned_cols)) // total_croppers
        cols = owned_cols[start:end] if end > start else [owned_cols[min(start, len(owned_cols) - 1)]]
        return set(cols)

    def nearest(pos, lst, exclude):
        best, bd = None, 10 ** 9
        for t in lst:
            if t in exclude:
                continue
            d = abs(t[0] - pos[0]) + abs(t[1] - pos[1])
            if d < bd:
                bd, best = d, t
        return best

    def nearest_in(pos, lst, exclude, cols):
        best, bd = None, 10 ** 9
        for t in lst:
            if t in exclude:
                continue
            if cols is not None and t[0] not in cols:
                continue
            d = abs(t[0] - pos[0]) + abs(t[1] - pos[1])
            if d < bd:
                bd, best = d, t
        return best

    # ---- setup helper: place a bought animal from shed onto a structure ----
    def setup_animal(pos, inv):
        fx, fy = pos
        # Which animal are we carrying / need to place?
        carry = None
        for an in ANIMAL:
            if inv.get(an, 0) > 0:
                carry = an
                break
        if carry is None:
            # pick one up from the shed if any waiting
            for an in ANIMAL:
                if shed.get(an, 0) > 0:
                    if pos in shed_set:
                        return ["PICKUP", an, 1]
                    s = nearest_shed_tile(fx, fy)
                    return moveto(fx, fy, *s)
            return None
        # carrying an animal: find/make a home near the shed
        struct = ANIMAL[carry]["structure"]
        # existing empty matching structure?
        tgt = None
        for (x, y, t) in empty_structs:
            if (x, y) in claimed_animals:
                continue
            if t.get("kind") == struct:
                tgt = (x, y)
                break
        if tgt is not None:
            if pos == tgt:
                claimed_animals.add(tgt)
                return ["PLACE", carry, 1]
            return moveto(fx, fy, *tgt)
        # else build a structure on the nearest empty owned tile close to shed
        # prefer tiles adjacent to shed for feed logistics
        cand = None
        cb = 10 ** 9
        for (x, y) in empty_tiles:
            if (x, y) in claimed_animals:
                continue
            d = abs(x - (half - 1)) + abs(y - (half - 1))
            if d < cb:
                cb, cand = d, (x, y)
        if cand is not None:
            if pos == cand:
                claimed_animals.add(cand)
                return ["BUILD_PASTURE"] if struct == "PASTURE" else ["BUILD_COOP"]
            return moveto(fx, fy, *cand)
        return None

    # ---- feeder behavior: fetch wheat, service animals ----
    def feed_unit(pos, inv):
        fx, fy = pos
        # If we're mid-setup (carrying/placing an animal), handle that first.
        s = setup_animal(pos, inv)
        if s is not None:
            return s
        # Service an animal we're standing on.
        cur = T(fx, fy)
        if is_animal(cur) and (fx, fy) not in claimed_animals:
            if not cur.get("fed_today", False) and inv.get("WHEAT", 0) > 0:
                claimed_animals.add((fx, fy))
                return ["FEED"]
            if cur.get("fed_today", False) and not cur.get("cared_today", False):
                return ["CARE"]
            if _num(cur.get("yield_units", 0), 0) > 0:
                return ["HARVEST"]
            if cur.get("fertilizer_available", False):
                return ["COLLECT_FERTILIZER"]
        # Need wheat to feed? Fetch from shed.
        unfed = [(x, y) for (x, y) in feed_needed if (x, y) not in claimed_animals]
        if unfed and inv.get("WHEAT", 0) == 0:
            if pos in shed_set:
                if shed.get("WHEAT", 0) > 0:
                    return ["PICKUP", "WHEAT", min(4, shed.get("WHEAT", 0))]
                return ["PASS"]  # wait for wheat delivery
            return moveto(fx, fy, *nearest_shed_tile(fx, fy))
        # Go to the nearest animal needing feed (we have wheat) or needing harvest/collect.
        tgt = nearest(pos, unfed, claimed_animals)
        if tgt is None:
            tgt = nearest(pos, [(x, y) for (x, y) in animal_harvest if (x, y) not in claimed_animals], set())
        if tgt is None:
            tgt = nearest(pos, [(x, y) for (x, y) in fert_ready if (x, y) not in claimed_animals], set())
        if tgt is not None:
            if pos == tgt:
                cur = T(tgt[0], tgt[1])
                if is_animal(cur):
                    if not cur.get("fed_today", False) and inv.get("WHEAT", 0) > 0:
                        claimed_animals.add(tgt)
                        return ["FEED"]
                    if _num(cur.get("yield_units", 0), 0) > 0:
                        return ["HARVEST"]
                    if cur.get("fertilizer_available", False):
                        return ["COLLECT_FERTILIZER"]
                return ["PASS"]
            return moveto(fx, fy, *tgt)
        # nothing to do for animals: help with crops
        return None

    # ---- cropper behavior: lane-based water/harvest/plant/weed ----
    def crop_unit(pos, inv, cols):
        fx, fy = pos
        carrying = inv and sum(inv.values()) > 0
        if carrying and pos in shed_set:
            return ["DROP"]
        for lane_only in (True, False):
            use_cols = cols if lane_only else None
            b = nearest_in(pos, water_list, claimed, use_cols)
            if b is not None:
                if b == pos:
                    claimed.add(b)
                    return ["WATER"]
                return moveto(fx, fy, *b)
            b = nearest_in(pos, harvest_list, claimed, use_cols)
            if b is not None:
                if b == pos:
                    claimed.add(b)
                    return ["HARVEST"]
                return moveto(fx, fy, *b)
            crop = best_crop_to_plant()
            if crop is not None:
                b = nearest_in(pos, empty_tiles, claimed, use_cols)
                if b is not None:
                    if b == pos:
                        claimed.add(b)
                        seeds_left[crop] = seeds_left.get(crop, 0) - 1
                        return ["PLANT", crop]
                    return moveto(fx, fy, *b)
            b = nearest_in(pos, weed_tiles, claimed, use_cols)
            if b is not None:
                if b == pos:
                    claimed.add(b)
                    return ["DIG"]
                return moveto(fx, fy, *b)
        if carrying:
            if pos in shed_set:
                return ["DROP"]
            return moveto(fx, fy, *nearest_shed_tile(fx, fy))
        return ["PASS"]

    # Assign: first n_feeders units are feeders; rest are croppers.
    actions = []
    cropper_idx = 0
    total_croppers = max(0, n_units - n_feeders)
    for ui in range(n_units):
        pos, inv = units[ui]
        act = None
        if ui < n_feeders and n_animals > 0:
            act = feed_unit(pos, inv)
        if act is None:
            # setup animals even if a cropper happens to be free and one is waiting
            if any(shed.get(an, 0) > 0 for an in ANIMAL) or any(inv.get(an, 0) > 0 for an in ANIMAL):
                s = setup_animal(pos, inv)
                if s is not None:
                    act = s
        if act is None:
            cols = crop_lane(cropper_idx, total_croppers) if total_croppers > 0 else set(owned_cols)
            cropper_idx += 1
            act = crop_unit(pos, inv, cols)
        actions.append(act if act is not None else ["PASS"])

    farmer_action = actions[0] if actions else ["PASS"]
    hands_actions = actions[1:] if len(actions) > 1 else []
    return {"farmer": farmer_action, "hands": hands_actions, "market": market}


def _seed_plan(day, money, tiles, N, owned, is_plant, seeds, melon_now,
               straw_now, plants_now, empty_owned, n_animals, hires_today):
    orders = []
    # capacity: units can tend ~7 crop tiles each; reserve some units for feeding.
    crew = 1 + min(CREW_CAP, hires_today if hires_today > 0 else 3)
    feeders = 0
    if n_animals > 0:
        feeders = max(1, (n_animals + 2) // 3)
    croppers = max(1, crew - feeders)
    cap = croppers * 7
    room = max(0, cap - plants_now)
    current_seeds = sum(seeds.get(c, 0) for c in CROP)
    plantable = max(0, min(empty_owned, room) - current_seeds)
    if plantable <= 0:
        return orders
    buffer = 120 + n_animals * 20  # keep more cash when running a herd
    spend = max(0, money - buffer)

    if day <= LAST_DAY - CROP["MELON"]["grow"]:
        room_m = max(0, MELON_CAP - melon_now - seeds.get("MELON", 0))
        n = min(plantable, room_m, int(spend // CROP["MELON"]["seed"]))
        if n > 0:
            orders.append(["BUY_SEED", "MELON", n])
            spend -= n * CROP["MELON"]["seed"]
            plantable -= n
    if plantable > 0 and spend > CROP["STRAWBERRY"]["seed"] and 3 <= day <= LAST_DAY - 12:
        room_s = max(0, STRAW_CAP - straw_now - seeds.get("STRAWBERRY", 0))
        n = min(plantable, room_s, int(spend // CROP["STRAWBERRY"]["seed"]), 2)
        if n > 0:
            orders.append(["BUY_SEED", "STRAWBERRY", n])
            spend -= n * CROP["STRAWBERRY"]["seed"]
            plantable -= n
    if plantable > 0 and spend > 0:
        if day <= LAST_DAY - CROP["WHEAT"]["grow"]:
            n = min(plantable, int(spend // CROP["WHEAT"]["seed"]))
            if n > 0:
                orders.append(["BUY_SEED", "WHEAT", n])
        elif day <= LAST_DAY - CROP["CARROT"]["grow"]:
            n = min(plantable, int(spend // CROP["CARROT"]["seed"]))
            if n > 0:
                orders.append(["BUY_SEED", "CARROT", n])
    return orders


# Keep `agent` as the LAST callable so the kaggle file-loader binds the real entry point.
def agent(obs):
    return _agent_impl(obs)
