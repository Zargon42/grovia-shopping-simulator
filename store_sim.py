#!/usr/bin/env python3
"""
Supermarket routing simulator.

Compares seven ways of walking a shopping list through a store:

  S0  List order            shortest path between consecutive items, in the order written
  S1  Unconstrained tour    shortest tour over all items, ignoring product category
  S2  Constrained tour      your proposal: category groups in a fixed order, shortest tour inside each group
  S3  Fixed store walk      the habitual shopper: picks items in the order a fixed walk through the store passes them
  S4  Staple + aisle search  picks familiar staples, then searches relevant aisles
  S5  Empirical excursions  near-shortest purchase order with aisle detours calibrated to observed travel deviation
  S6  Stochastic shopper    sampled shopper profiles with probabilistic target choice and occasional browsing detours

All results come from a simulated store and simulated lists. Label them "simulated" on the slides.

USAGE
  python store_sim.py                                   # default synthetic grid store
  python store_sim.py --aisles 14 --aisle-length 42     # different grid
  python store_sim.py --zones frozen_at_entrance        # different department layout
  python store_sim.py --sweep-zones                     # run every built-in layout and compare
  python store_sim.py --export-store my_store.json      # write the default store as an editable JSON file
  python store_sim.py --store my_store.json             # run on your own layout
  python store_sim.py --store my_store.json --catalog my_catalog.csv --config my_config.json

CUSTOM STORE (JSON)
  {
    "name": "coop_padova",
    "entrance": "n1",
    "checkouts": ["n40", "n41"],
    "walk": ["n1", "n2", ...],                 # optional habitual path for S3; if missing, one is computed
    "nodes": {
      "n1": {"x": 0, "y": 0, "aisle": null, "stocks": ["fresh"]},   # x, y in meters
      "n2": {"x": 0, "y": 3, "aisle": "A1",  "stocks": ["dry", "heavy"]}
    },
    "edges": [["n1", "n2"], ["n2", "n3", 4.5]]  # weight in meters; omitted = straight-line distance
  }
  - A node is a place where a shopper can stand. Put one at every aisle end, junction, and shelf stop.
  - "aisle" is a label for the backtracking metric. Use null for cross aisles and open floor.
  - "stocks" lists the categories a shopper can pick at that node. A node can stock several.

CUSTOM CATALOG (CSV, optional)
    Columns: product,category,node,staple. The staple column is optional and accepts true/false.
    Without it, the script generates a synthetic catalog.

CUSTOM CONFIG (JSON, optional)
    Any field of the Config class below, for example:
    {"list_sizes": [8, 15, 30], "group_order": [["heavy"], ["dry"], ["fresh", "frozen"]], "pick_time_s": 15}
    For S4, staple_fraction marks the first fraction of synthetic catalog products as staples.

OUTPUT (in --out, one folder per store)
  trials.csv, summary.csv, sensitivity.csv, report.txt, layout.png,
  distance_by_strategy.png, tradeoff_distance_vs_cold.png, heatmaps.png
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field, fields
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
from matplotlib.collections import LineCollection

END = "__END__"  # pseudo node: the nearest checkout, whichever it is


# --------------------------------------------------------------------------- config
@dataclass
class Config:
    # A realistic weekly basket in the US online dataset is smaller than the older 10-40 item default.
    list_sizes: list = field(default_factory=lambda: [6, 12, 24, 40])
    list_types: list = field(default_factory=lambda: ["random", "grouped"])
    n_lists: int = 100
    n_products: int = 1000
    seed: int = 42
    # Older trip data implies a slower overall effective pace than a pure walking speed, so keep the
    # walking speed realistic while allowing longer pick times than the original 12 s assumption.
    speed_mps: float = 0.8
    pick_time_s: float = 18.0
    # Synthetic staple products approximate pasta/bread (dry) and meat/vegetables (fresh).
    staple_categories: list = field(default_factory=lambda: ["dry", "fresh"])
    staple_fraction: float = 0.20
    # Optional exact product IDs for a custom synthetic experiment; overrides category selection.
    staple_ids: list | None = None
    # A rough US online mix, based on produce/dairy, snacks, beverages, frozen, and a smaller heavy share.
    category_mix: dict = field(default_factory=lambda: {
        "dry": 0.26, "beverage": 0.14, "fresh": 0.36, "frozen": 0.14, "heavy": 0.10})
    # S2 visits these groups in this order. Every category in the catalog must appear once.
    group_order: list = field(default_factory=lambda: [["heavy"], ["dry", "beverage"], ["fresh"], ["frozen"]])
    # Mean travel-deviation share reported for tracked shoppers in SSRN 942570.
    s5_travel_deviation_share: float = 0.69
    # Scenario weights for S6; these are assumptions, not estimates from the cited studies.
    s6_profile_weights: dict = field(default_factory=lambda: {
        "focused": 0.55, "familiar": 0.30, "exploratory": 0.15})
    s6_max_detour_m: float = 18.0
    cold_categories: list = field(default_factory=lambda: ["fresh", "frozen"])
    frozen_categories: list = field(default_factory=lambda: ["frozen"])
    sens_speeds: list = field(default_factory=lambda: [0.6, 0.8, 1.0, 1.2])
    sens_picks: list = field(default_factory=lambda: [10, 18, 25, 35])


def load_config(path: str | None) -> Config:
    if not path:
        return Config()
    data = json.loads(Path(path).read_text())
    known = {f.name for f in fields(Config)}
    bad = set(data) - known
    if bad:
        raise ValueError(f"Unknown config fields: {sorted(bad)}. Valid fields: {sorted(known)}")
    return Config(**data)


# --------------------------------------------------------------------------- store
@dataclass(frozen=True)
class Product:
    pid: int
    cat: str
    node: str
    staple: bool = False


class Store:
    """A store is a weighted graph. Nodes are places to stand; edges are walkable segments in meters."""

    def __init__(self, name: str = "store"):
        self.name = name
        self.G = nx.Graph()
        self.entrance: str | None = None
        self.checkouts: list[str] = []
        self.walk: list[str] | None = None
        self.D: dict = {}
        self._paths: dict = {}

    def add_node(self, node, x, y, aisle=None, stocks=()):
        self.G.add_node(node, x=float(x), y=float(y), aisle=aisle, stocks=set(stocks))

    def add_edge(self, a, b, weight=None):
        if weight is None:
            pa, pb = self.G.nodes[a], self.G.nodes[b]
            weight = float(np.hypot(pa["x"] - pb["x"], pa["y"] - pb["y"]))
        self.G.add_edge(a, b, weight=float(weight))

    def categories(self) -> set:
        return set().union(*(d["stocks"] for _, d in self.G.nodes(data=True))) if self.G else set()

    def finalize(self) -> "Store":
        if self.entrance not in self.G:
            raise ValueError(f"Entrance {self.entrance!r} is not a node")
        missing = [c for c in self.checkouts if c not in self.G]
        if not self.checkouts or missing:
            raise ValueError(f"Checkouts missing or unknown: {missing or 'none given'}")
        if not nx.is_connected(self.G):
            comps = [len(c) for c in nx.connected_components(self.G)]
            raise ValueError(f"Store graph is not connected (component sizes: {comps}). Check the edges.")
        self.D = {u: dict(d) for u, d in nx.all_pairs_dijkstra_path_length(self.G, weight="weight")}
        for u in self.D:
            self.D[u][END] = min(self.D[u][c] for c in self.checkouts)
        self._paths = {}
        if self.walk is None:
            self.walk = self._default_walk()
        if not self.walk or self.walk[0] != self.entrance:
            raise ValueError("Fixed store walk must start at the entrance")
        unknown_walk_nodes = [n for n in self.walk if n not in self.G]
        if unknown_walk_nodes:
            raise ValueError(f"Fixed store walk contains unknown nodes: {unknown_walk_nodes[:5]}")
        disconnected_walk = [(a, b) for a, b in zip(self.walk, self.walk[1:]) if not self.G.has_edge(a, b)]
        if disconnected_walk:
            raise ValueError(f"Fixed store walk contains non-adjacent nodes: {disconnected_walk[0]}")
        missing_walk_nodes = [n for n, d in self.G.nodes(data=True) if d["stocks"] and n not in self.walk]
        if missing_walk_nodes:
            raise ValueError(f"Fixed store walk omits stocked nodes: {missing_walk_nodes[:5]}")
        return self

    def _default_walk(self) -> list[str]:
        nodes = [n for n, d in self.G.nodes(data=True) if d["stocks"]]
        seq = nn_order([Product(-1, "", n) for n in nodes], self.entrance, self.D)
        two_opt(seq, self.D, self.entrance, END)
        return [self.entrance] + [p.node for p in seq]

    def path(self, a: str, b: str) -> list[str]:
        if b == END:
            b = min(self.checkouts, key=lambda c: self.D[a][c])
        key = (a, b)
        if key not in self._paths:
            self._paths[key] = nx.shortest_path(self.G, a, b, weight="weight")
        return self._paths[key]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "entrance": self.entrance,
            "checkouts": self.checkouts,
            "walk": self.walk,
            "nodes": {n: {"x": d["x"], "y": d["y"], "aisle": d["aisle"], "stocks": sorted(d["stocks"])}
                      for n, d in self.G.nodes(data=True)},
            "edges": [[u, v, round(d["weight"], 3)] for u, v, d in self.G.edges(data=True)],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Store":
        s = cls(d.get("name", "custom"))
        for n, a in d["nodes"].items():
            s.add_node(n, a["x"], a["y"], a.get("aisle"), a.get("stocks", []))
        for e in d["edges"]:
            s.add_edge(e[0], e[1], e[2] if len(e) > 2 else None)
        s.entrance = d["entrance"]
        s.checkouts = list(d["checkouts"])
        s.walk = d.get("walk")
        return s.finalize()


# Department placement for the grid generator.
# rows: "front" (entrance wall), "back", "interior" (inside the aisles), "all"
# aisle_frac: [lo, hi) as fractions of the aisle count, so a zone map scales with the number of aisles
# aisle_ids: explicit list of aisle indices
ZONE_PRESETS = {
    "default": {
        "fresh": [{"rows": "front"}, {"rows": "back"}],
        "dry": [{"rows": "interior", "aisle_frac": [0.0, 0.7]}],
        "beverage": [{"rows": "interior", "aisle_frac": [0.3, 0.7]}],
        "heavy": [{"rows": "interior", "aisle_frac": [0.2, 0.6]}],
        "frozen": [{"rows": "interior", "aisle_frac": [0.7, 1.0]}],
    },
    "fresh_front": {  # produce at the entrance, frozen along the back wall
        "fresh": [{"rows": "front"}],
        "dry": [{"rows": "interior", "aisle_frac": [0.0, 0.7]}],
        "beverage": [{"rows": "interior", "aisle_frac": [0.3, 1.0]}],
        "heavy": [{"rows": "interior", "aisle_frac": [0.3, 0.8]}],
        "frozen": [{"rows": "back"}],
    },
    "frozen_at_entrance": {  # worst case for the "frozen last" rule
        "fresh": [{"rows": "back"}],
        "frozen": [{"rows": "interior", "aisle_frac": [0.0, 0.2]}],
        "dry": [{"rows": "interior", "aisle_frac": [0.2, 0.8]}],
        "beverage": [{"rows": "interior", "aisle_frac": [0.5, 1.0]}],
        "heavy": [{"rows": "interior", "aisle_frac": [0.6, 1.0]}],
    },
}


def _zone_match(sel: dict, col: int, n_cols: int, row: int, n_rows: int) -> bool:
    kind = sel.get("rows", "all")
    if kind == "front" and row != 0:
        return False
    if kind == "back" and row != n_rows - 1:
        return False
    if kind == "interior" and row in (0, n_rows - 1):
        return False
    ids = sel.get("aisle_ids")
    if ids is not None and col not in ids:
        return False
    frac = sel.get("aisle_frac")
    if frac and not (frac[0] <= (col + 0.5) / n_cols < frac[1]):
        return False
    return True


def load_zones(arg: str) -> dict | str:
    if arg in ZONE_PRESETS:
        return arg
    p = Path(arg)
    if p.exists():
        return json.loads(p.read_text())
    raise ValueError(f"--zones must be one of {list(ZONE_PRESETS)} or a JSON file path")


def make_grid_store(aisles=10, aisle_length=30.0, aisle_spacing=3.0, stop_step=3.0,
                    zones="default", n_checkouts=1, name=None) -> Store:
    """Parallel aisles joined by a front and a back cross aisle. Entrance front-left, checkouts front-right."""
    zone_map = ZONE_PRESETS[zones] if isinstance(zones, str) else zones
    rows = int(round(aisle_length / stop_step)) + 1
    if rows < 3 or aisles < 1:
        raise ValueError("Need at least 3 rows (aisle_length / stop_step >= 2) and 1 aisle")
    label = zones if isinstance(zones, str) else "custom_zones"
    s = Store(name or f"grid_{aisles}aisles_{int(aisle_length)}m_{label}")
    nid = lambda a, r: f"A{a}R{r}"
    for a in range(aisles):
        for r in range(rows):
            stocks = {c for c, sels in zone_map.items()
                      if any(_zone_match(sel, a, aisles, r, rows) for sel in sels)}
            cross = r in (0, rows - 1)
            s.add_node(nid(a, r), a * aisle_spacing, r * stop_step, None if cross else a, stocks)
    for a in range(aisles):
        for r in range(rows - 1):
            s.add_edge(nid(a, r), nid(a, r + 1), stop_step)
    for a in range(aisles - 1):
        for r in (0, rows - 1):
            s.add_edge(nid(a, r), nid(a + 1, r), aisle_spacing)
    s.entrance = nid(0, 0)
    s.checkouts = [nid(aisles - 1 - i, 0) for i in range(min(n_checkouts, aisles))]
    # Habitual walk: serpentine through every aisle in order.
    s.walk = []
    for a in range(aisles):
        rr = range(rows) if a % 2 == 0 else range(rows - 1, -1, -1)
        s.walk += [nid(a, r) for r in rr]
    return s.finalize()


# --------------------------------------------------------------------------- catalog and lists
def make_catalog(store: Store, cfg: Config, rng: random.Random) -> list[Product]:
    nodes_by_cat = defaultdict(list)
    for n, d in store.G.nodes(data=True):
        for c in d["stocks"]:
            nodes_by_cat[c].append(n)
    cats = [c for c in cfg.category_mix if c in nodes_by_cat]
    if not cats:
        raise ValueError("No category in category_mix is stocked anywhere in the store")
    weights = [cfg.category_mix[c] for c in cats]
    staple_period = max(1, round(1 / cfg.staple_fraction)) if cfg.staple_fraction > 0 else None
    out = []
    for pid in range(cfg.n_products):
        c = rng.choices(cats, weights=weights)[0]
        periodic_staple = staple_period is not None and pid % staple_period == 0
        staple = pid in cfg.staple_ids if cfg.staple_ids is not None else (
            c in cfg.staple_categories and periodic_staple)
        out.append(Product(pid, c, rng.choice(nodes_by_cat[c]), staple))
    return out


def load_catalog_csv(path: str, store: Store) -> list[Product]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f)):
            if row["node"] not in store.G:
                raise ValueError(f"Catalog row {i + 2}: node {row['node']!r} is not in the store")
            staple = row.get("staple", "").strip().lower() in {"1", "true", "yes", "y"}
            out.append(Product(i, row["category"], row["node"], staple))
    return out


def sample_items(by_cat: dict, mix: dict, n: int, rng: random.Random) -> list[Product]:
    cats = [c for c in mix if c in by_cat]
    counts = Counter(rng.choices(cats, weights=[mix[c] for c in cats], k=n))
    items = []
    for c, k in counts.items():
        items += rng.sample(by_cat[c], min(k, len(by_cat[c])))
    return items


def order_list(items: list[Product], list_type: str, rng: random.Random) -> list[Product]:
    items = list(items)
    rng.shuffle(items)  # "random": written as the shopper remembered
    if list_type == "grouped":  # a careful shopper who writes the list by category
        items.sort(key=lambda p: p.cat)
    return items


# --------------------------------------------------------------------------- routing
def nn_order(items, start, D):
    todo, cur, out = list(items), start, []
    while todo:
        k = min(range(len(todo)), key=lambda i: D[cur][todo[i].node])
        p = todo.pop(k)
        out.append(p)
        cur = p.node
    return out


def two_opt(seq, D, start, end, seg=None, max_passes=50):
    """2-opt on an open path from `start` to `end`.
    `seg` labels each position with a group. A reversal is allowed only inside one group,
    so improvements never break the group order."""
    n = len(seq)
    if n < 2:
        return seq
    seg = seg or [0] * n
    for _ in range(max_passes):
        improved = False
        for i in range(n - 1):
            a = start if i == 0 else seq[i - 1].node
            b = seq[i].node
            for j in range(i + 1, n):
                if seg[j] != seg[i]:
                    break
                c = seq[j].node
                d = end if j == n - 1 else seq[j + 1].node
                if D[a][c] + D[b][d] < D[a][b] + D[c][d] - 1e-9:
                    seq[i:j + 1] = seq[i:j + 1][::-1]
                    b = seq[i].node
                    improved = True
        if not improved:
            break
    return seq


def staple_dfs_walk(store: Store, items: list[Product], cfg: Config):
    """Use S2 for staples, then explore relevant aisles one at a time."""
    staple_items = [p for p in items if p.staple]
    staple_order = s2(store, staple_items, cfg) if staple_items else []
    known_nodes = [store.entrance]
    current = store.entrance
    for product in staple_order:
        segment = store.path(current, product.node)
        known_nodes.extend(segment[1:])
        current = product.node

    remaining = {p.pid: p for p in items}
    order = []

    def pick_at(node):
        for p in items:
            if p.pid in remaining and p.node == node:
                order.append(p)
                del remaining[p.pid]

    for node in known_nodes:
        pick_at(node)

    search_nodes = []
    aisle_nodes = defaultdict(list)
    for node, data in store.G.nodes(data=True):
        area = data["aisle"] if data["aisle"] is not None else "__OPEN_AREA__"
        if data["stocks"]:
            aisle_nodes[area].append(node)
    for aisle in aisle_nodes:
        aisle_nodes[aisle].sort(key=lambda node: store.G.nodes[node]["y"])
    searched_aisles = set()

    def add_path(path):
        if not path:
            return
        search_nodes.extend(path if not search_nodes else path[1:])
        for node in path:
            pick_at(node)

    current = known_nodes[-1]
    while remaining:
        target_categories = {p.cat for p in remaining.values()}
        relevant_aisles = []
        for aisle, nodes in list(aisle_nodes.items()):
            if aisle in searched_aisles:
                continue
            aisle_categories = set().union(*(store.G.nodes[n]["stocks"] for n in nodes))
            if aisle_categories & target_categories:
                endpoints = [nodes[0], nodes[-1]]
                entry = min(endpoints, key=lambda n: store.D[current][n])
                relevant_aisles.append((store.D[current][entry], aisle, entry))
        if not relevant_aisles:
            break
        _, aisle, entry = min(relevant_aisles, key=lambda candidate: candidate[0])
        add_path(store.path(current, entry))
        current = entry

        nodes = aisle_nodes[aisle]
        searched_aisles.add(aisle)
        direction = nodes if entry == nodes[0] else list(reversed(nodes))
        aisle_categories = set().union(*(store.G.nodes[n]["stocks"] for n in nodes))
        for node in direction:
            if node != current:
                add_path(store.path(current, node))
            current = node
            if not remaining:
                break
            remaining_categories = {p.cat for p in remaining.values()}
            if not (aisle_categories & remaining_categories):
                break

    return order, known_nodes + (search_nodes[1:] if search_nodes else [])


@dataclass
class Strategy:
    id: str
    label: str
    fn: callable
    uses_list_order: bool = False
    walk_fn: callable | None = None


STRATEGIES: dict[str, Strategy] = {}


def strategy(sid, label, uses_list_order=False, walk_fn=None):
    def deco(fn):
        STRATEGIES[sid] = Strategy(sid, label, fn, uses_list_order, walk_fn)
        return fn
    return deco


@strategy("S0", "List order", uses_list_order=True)
def s0(store, items, cfg):
    return list(items)


@strategy("S1", "Shortest tour, ignores category")
def s1(store, items, cfg):
    seq = nn_order(items, store.entrance, store.D)
    return two_opt(seq, store.D, store.entrance, END)


@strategy("S2", "Category-constrained tour")
def s2(store, items, cfg):
    seq, seg, cur = [], [], store.entrance
    for gi, group in enumerate(cfg.group_order):
        part = nn_order([p for p in items if p.cat in group], cur, store.D)
        seq += part
        seg += [gi] * len(part)
        if part:
            cur = part[-1].node
    return two_opt(seq, store.D, store.entrance, END, seg)


@strategy("S3", "Fixed store walk (habitual shopper)")
def s3(store, items, cfg):
    rank = {}
    for i, n in enumerate(store.walk):
        rank.setdefault(n, i)
    return sorted(items, key=lambda p: rank.get(p.node, len(store.walk)))


@strategy("S4", "S2 staple route, category-guided aisle search")
def s4(store, items, cfg):
    order, _ = staple_dfs_walk(store, items, cfg)
    return order


def perimeter_excursion_walk(store: Store, items: list[Product], cfg: Config) -> tuple[list[Product], list[str]]:
    """Approximate empirical travel deviations with short out-and-back aisle excursions."""
    if not 0 <= cfg.s5_travel_deviation_share < 1:
        raise ValueError("s5_travel_deviation_share must be at least 0 and less than 1")

    base_order = s1(store, items, cfg)
    stops = [store.entrance] + [p.node for p in base_order] + [END]
    base_nodes = [store.entrance]
    for a, b in zip(stops, stops[1:]):
        base_nodes.extend(store.path(a, b)[1:])
    base_distance = sum(store.G[a][b]["weight"] for a, b in zip(base_nodes, base_nodes[1:]))
    target_excursion_distance = (
        base_distance * cfg.s5_travel_deviation_share / (1 - cfg.s5_travel_deviation_share)
    )

    excursion_options = []
    seen_entries = set()
    for anchor, data in store.G.nodes(data=True):
        if data["aisle"] is not None:
            continue
        index = min(range(len(base_nodes)),
                    key=lambda i: (store.D[base_nodes[i]][anchor], i))
        connector = store.path(base_nodes[index], anchor)
        for first in sorted(store.G.neighbors(anchor), key=str):
            if store.G.nodes[first]["aisle"] is None:
                continue
            entry_key = (anchor, first)
            if entry_key in seen_entries:
                continue
            seen_entries.add(entry_key)

            outbound = connector + [first]
            previous, current = anchor, first
            while True:
                next_nodes = [
                    n for n in store.G.neighbors(current)
                    if n != previous
                    and store.G.nodes[n]["aisle"] is not None
                    and abs(store.G.nodes[n]["x"] - store.G.nodes[first]["x"]) < 1e-9
                ]
                if not next_nodes:
                    break
                next_node = min(next_nodes, key=lambda n: (store.G.nodes[n]["y"], str(n)))
                if next_node in outbound:
                    break
                outbound.append(next_node)
                previous, current = current, next_node

            options = []
            for depth in range(1, len(outbound)):
                out_path = outbound[:depth + 1]
                cycle = out_path + list(reversed(out_path[:-1]))
                distance = sum(store.G[a][b]["weight"] for a, b in zip(cycle, cycle[1:]))
                options.append((distance, cycle))
            if options:
                excursion_options.append((index, str(anchor), str(first), options))

    selected = []
    used_entries = set()
    remaining_distance = target_excursion_distance
    while True:
        choices = []
        for index, anchor, first, options in excursion_options:
            key = (anchor, first)
            if key in used_entries:
                continue
            for distance, cycle in options:
                if distance <= remaining_distance + 1e-9:
                    choices.append((distance, index, anchor, first, cycle))
        if not choices:
            break
        distance, index, anchor, first, cycle = max(
            choices, key=lambda choice: (choice[0], -choice[1], choice[2], choice[3])
        )
        selected.append((index, cycle))
        used_entries.add((anchor, first))
        remaining_distance -= distance

    excursions_by_index = defaultdict(list)
    for index, cycle in selected:
        excursions_by_index[index].append(cycle)
    full = []
    for index, node in enumerate(base_nodes):
        full.append(node)
        for cycle in excursions_by_index[index]:
            full.extend(cycle[1:])

    remaining = {p.pid: p for p in items}
    order = []
    for node in full:
        for product in items:
            if product.pid in remaining and product.node == node:
                order.append(product)
                del remaining[product.pid]
    if remaining:
        raise ValueError(f"S5 route did not visit product nodes for IDs {sorted(remaining)}")
    return order, full


@strategy("S5", "Empirical travel-deviation aisle excursions")
def s5(store, items, cfg):
    """Use a good purchase order, with additional short aisle excursions while walking."""
    order, _ = perimeter_excursion_walk(store, items, cfg)
    return order


S6_PROFILES = {
    "focused": {
        "distance_scale_m": 8.0, "list_weight": 0.32, "category_bonus": 0.12,
        "staple_bonus": 0.05, "choice_noise": 0.30, "detour_probability": 0.05,
    },
    "familiar": {
        "distance_scale_m": 10.0, "list_weight": 0.18, "category_bonus": 0.38,
        "staple_bonus": 0.55, "choice_noise": 0.55, "detour_probability": 0.12,
    },
    "exploratory": {
        "distance_scale_m": 16.0, "list_weight": 0.08, "category_bonus": 0.20,
        "staple_bonus": 0.12, "choice_noise": 0.95, "detour_probability": 0.28,
    },
}


def validate_s6_profile_weights(weights: dict):
    if not isinstance(weights, dict) or set(weights) != set(S6_PROFILES):
        raise ValueError(
            "s6_profile_weights must contain focused, familiar, and exploratory"
        )
    if any(
        isinstance(weight, bool)
        or not isinstance(weight, (int, float))
        or not np.isfinite(weight)
        or weight < 0
        for weight in weights.values()
    ) or sum(weights.values()) <= 0:
        raise ValueError(
            "s6_profile_weights must have non-negative numeric weights and a positive total"
        )


def _aisle_excursions(store: Store, products: list[Product]) -> dict[str, list[tuple[list[str], float]]]:
    """Return short out-and-back aisle paths from open-floor junctions."""
    product_nodes = {p.node for p in products}
    excursions = defaultdict(list)
    for anchor, data in store.G.nodes(data=True):
        if data["aisle"] is not None:
            continue
        for first in store.G.neighbors(anchor):
            aisle = store.G.nodes[first]["aisle"]
            if aisle is None:
                continue
            path = [anchor, first]
            previous, current = anchor, first
            while len(path) < 4:
                next_nodes = [
                    node for node in store.G.neighbors(current)
                    if node != previous
                    and store.G.nodes[node]["aisle"] == aisle
                    and node not in path
                ]
                if not next_nodes:
                    break
                next_node = min(
                    next_nodes,
                    key=lambda node: (store.G.nodes[node]["y"], str(node)),
                )
                path.append(next_node)
                previous, current = current, next_node
            if any(node in product_nodes for node in path[1:]):
                continue
            return_path = store.path(path[-1], anchor)
            if any(node in product_nodes for node in return_path[1:-1]):
                continue
            cycle = path + return_path[1:]
            distance = sum(
                store.G[a][b]["weight"] for a, b in zip(cycle, cycle[1:])
            )
            excursions[anchor].append((cycle, distance))
    return excursions


def stochastic_shopper_trip(store: Store, items: list[Product], cfg: Config):
    """Build one deterministic, literature-informed scenario trip for S6."""
    if not items:
        return [], [store.entrance], "focused", []
    profile_weights = cfg.s6_profile_weights
    validate_s6_profile_weights(profile_weights)

    seed_items = ",".join(str(pid) for pid in sorted(p.pid for p in items))
    rng = random.Random(f"{cfg.seed}|S6|{seed_items}")
    profile = rng.choices(
        list(profile_weights), weights=list(profile_weights.values()), k=1
    )[0]
    traits = S6_PROFILES[profile]
    list_position = {p.pid: index for index, p in enumerate(items)}
    remaining = list(items)
    order = []
    physical_route = [store.entrance]
    pickup_distances = []
    current = store.entrance
    walked = 0.0
    excursions = _aisle_excursions(store, items)

    while remaining:
        progress = len(order) / len(items)
        choice_noise = traits["choice_noise"] * (1.0 - 0.7 * progress)
        candidates = []
        for product in remaining:
            distance = store.D[current][product.node]
            list_gap = max(0, list_position[product.pid] - len(order))
            score = (
                -distance / traits["distance_scale_m"]
                - traits["list_weight"] * list_gap
                + traits["category_bonus"] * bool(order and order[-1].cat == product.cat)
                + traits["staple_bonus"] * product.staple
                + rng.gauss(0.0, choice_noise)
            )
            candidates.append((score, product))
        selected = max(candidates, key=lambda candidate: candidate[0])[1]

        shortest = store.path(current, selected.node)
        route_to_target = shortest
        anchors = {node: index for index, node in enumerate(shortest)
                   if node in excursions}
        eligible = []
        if rng.random() < traits["detour_probability"] * (1.0 - 0.7 * progress):
            for anchor, index in anchors.items():
                for cycle, cycle_distance in excursions[anchor]:
                    approach_extra = (
                        store.D[current][anchor] + store.D[anchor][selected.node]
                        - store.D[current][selected.node]
                    )
                    extra = approach_extra + cycle_distance
                    if extra <= cfg.s6_max_detour_m:
                        eligible.append((extra, index, anchor, cycle))
        if eligible:
            _, index, anchor, cycle = rng.choice(eligible)
            route_to_target = (
                shortest[:index + 1]
                + cycle[1:]
                + store.path(anchor, selected.node)[1:]
            )

        for a, b in zip(route_to_target, route_to_target[1:]):
            walked += store.G[a][b]["weight"]
        physical_route.extend(route_to_target[1:])
        current = selected.node
        order.append(selected)
        pickup_distances.append(walked)
        remaining.remove(selected)

    checkout_path = store.path(current, END)
    for a, b in zip(checkout_path, checkout_path[1:]):
        walked += store.G[a][b]["weight"]
    physical_route.extend(checkout_path[1:])
    return order, physical_route, profile, pickup_distances


@strategy("S6", "Stochastic shopper (literature-informed)", uses_list_order=True,
          walk_fn=stochastic_shopper_trip)
def s6(store, items, cfg):
    return stochastic_shopper_trip(store, items, cfg)[0]


# --------------------------------------------------------------------------- metrics
@dataclass
class Geometry:
    legs: list
    nodes: list
    retraced_m: float
    aisle_reentries: int
    item_remaining_m: list | None = None


def geometry(store: Store, order: list[Product], fixed_walk=False, staple_dfs=False,
             perimeter_excursion=False, cfg=None, physical_nodes=None,
             pickup_distances=None) -> Geometry:
    if physical_nodes is not None:
        if pickup_distances is None or len(pickup_distances) != len(order):
            raise ValueError("Explicit physical routes require one pickup distance per item")
        if any(not store.G.has_edge(a, b) for a, b in zip(physical_nodes, physical_nodes[1:])):
            raise ValueError("Explicit physical route contains a non-walkable segment")
        legs = [store.G[a][b]["weight"] for a, b in zip(physical_nodes, physical_nodes[1:])]
        total_distance = sum(legs)
        full = physical_nodes
        item_remaining_m = [total_distance - distance for distance in pickup_distances]
    elif perimeter_excursion:
        if cfg is None:
            raise ValueError("cfg is required for perimeter excursions")
        stops = [store.entrance] + [p.node for p in order] + [END]
        legs = [store.D[a][b] for a, b in zip(stops, stops[1:])]
        order, full = perimeter_excursion_walk(store, order, cfg)
        legs = [store.G[a][b]["weight"] for a, b in zip(full, full[1:])]
        walked_to = {}
        distance = 0.0
        for node, next_node in zip(full, full[1:]):
            walked_to.setdefault(node, distance)
            distance += store.G[node][next_node]["weight"]
        walked_to.setdefault(full[-1], distance)
        item_remaining_m = [distance - walked_to[p.node] for p in order]
    elif staple_dfs:
        if cfg is None:
            raise ValueError("cfg is required for staple DFS geometry")
        stops = [store.entrance] + [p.node for p in order] + [END]
        legs = [store.D[a][b] for a, b in zip(stops, stops[1:])]
        _, physical_nodes = staple_dfs_walk(store, order, cfg)
        physical_nodes = physical_nodes or [store.entrance]
        full = physical_nodes + store.path(physical_nodes[-1], END)[1:]
        legs = [store.G[a][b]["weight"] for a, b in zip(full, full[1:])]
        walked_to = {}
        distance = 0.0
        for node, next_node in zip(full, full[1:]):
            walked_to.setdefault(node, distance)
            distance += store.G[node][next_node]["weight"]
        walked_to.setdefault(full[-1], distance)
        item_remaining_m = [distance - walked_to[p.node] for p in order]
    elif fixed_walk:
        stops = [store.entrance] + [p.node for p in order] + [END]
        legs = [store.D[a][b] for a, b in zip(stops, stops[1:])]
        last_stop = max(store.walk.index(p.node) for p in order) if order else 0
        full = list(store.walk[:last_stop + 1])
        full.extend(store.path(full[-1], END)[1:])
        legs = [store.G[a][b]["weight"] for a, b in zip(full, full[1:])]
        walked_to = {}
        distance = 0.0
        for node, next_node in zip(full, full[1:]):
            walked_to.setdefault(node, distance)
            distance += store.G[node][next_node]["weight"]
        walked_to.setdefault(full[-1], distance)
        item_remaining_m = [distance - walked_to[p.node] for p in order]
    else:
        stops = [store.entrance] + [p.node for p in order] + [END]
        legs = [store.D[a][b] for a, b in zip(stops, stops[1:])]
        full = [store.entrance]
        for a, b in zip(stops, stops[1:]):
            full.extend(store.path(a, b)[1:])
        item_remaining_m = None
    edge_count = Counter()
    for u, v in zip(full, full[1:]):
        edge_count[(u, v) if u < v else (v, u)] += 1
    retraced = sum(store.G[u][v]["weight"] * (c - 1) for (u, v), c in edge_count.items() if c > 1)
    visits, cur = [], None
    for n in full:
        a = store.G.nodes[n]["aisle"]
        if a is None:
            cur = None
        elif a != cur:
            visits.append(a)
            cur = a
    return Geometry(legs, full, retraced, len(visits) - len(set(visits)), item_remaining_m)


def strategy_trip(store: Store, st: Strategy, items: list[Product], cfg: Config):
    if st.walk_fn is not None:
        order, nodes, profile, pickup_distances = st.walk_fn(store, items, cfg)
        geo = geometry(store, order, cfg=cfg, physical_nodes=nodes,
                       pickup_distances=pickup_distances)
        return order, geo, profile
    order = st.fn(store, items, cfg)
    geo = geometry(
        store,
        order,
        fixed_walk=(st.id == "S3"),
        staple_dfs=(st.id == "S4"),
        perimeter_excursion=(st.id == "S5"),
        cfg=cfg,
    )
    return order, geo, None


def timing(order, legs, speed, pick, cfg, item_remaining_m=None) -> dict:
    n, total = len(order), sum(legs)
    if item_remaining_m is None:
        suffix = np.cumsum(legs[::-1])[::-1]
        item_remaining_m = [suffix[i + 1] for i in range(n)]

    def exposure(cats):
        v = [(item_remaining_m[i] / speed + (n - 1 - i) * pick) / 60
             for i, p in enumerate(order) if p.cat in cats]
        return float(np.mean(v)) if v else float("nan")

    return {
        "walk_min": total / speed / 60,
        "total_min": (total / speed + n * pick) / 60,
        "cold_mean_min": exposure(cfg.cold_categories),      # minutes from pick to checkout, cold items
        "frozen_mean_min": exposure(cfg.frozen_categories),  # same, frozen items only
    }


# --------------------------------------------------------------------------- experiment
def validate(store: Store, catalog: list[Product], cfg: Config):
    if not 0 <= cfg.staple_fraction <= 1:
        raise ValueError("staple_fraction must be between 0 and 1")
    cats = {p.cat for p in catalog}
    grouped = [c for g in cfg.group_order for c in g]
    missing = cats - set(grouped)
    if missing:
        raise ValueError(f"Categories {sorted(missing)} are in the catalog but not in group_order")
    dup = [c for c, k in Counter(grouped).items() if k > 1]
    if dup:
        raise ValueError(f"Categories appear more than once in group_order: {dup}")
    validate_s6_profile_weights(cfg.s6_profile_weights)
    if (
        isinstance(cfg.s6_max_detour_m, bool)
        or not isinstance(cfg.s6_max_detour_m, (int, float))
        or not np.isfinite(cfg.s6_max_detour_m)
        or cfg.s6_max_detour_m < 0
    ):
        raise ValueError("s6_max_detour_m must be a finite non-negative distance in meters")


def run_experiment(store: Store, catalog: list[Product], cfg: Config, strategies=None, verbose=True):
    strategies = strategies or STRATEGIES
    validate(store, catalog, cfg)
    by_cat = defaultdict(list)
    for p in catalog:
        by_cat[p.cat].append(p)
    mix = {c: w for c, w in cfg.category_mix.items() if c in by_cat}
    mid = cfg.list_sizes[len(cfg.list_sizes) // 2]
    rows, sens = [], []
    visits = defaultdict(Counter)

    for size in cfg.list_sizes:
        if verbose:
            print(f"  [{store.name}] list size {size}: {cfg.n_lists} lists", flush=True)
        for t in range(cfg.n_lists):
            rng = random.Random(f"{cfg.seed}|{size}|{t}")
            items = sample_items(by_cat, mix, size, rng)
            canon = sorted(items, key=lambda p: p.pid)
            orders = {lt: order_list(items, lt, rng) for lt in cfg.list_types}
            cache = {}
            for sid, st in strategies.items():
                for lt in cfg.list_types:
                    if st.uses_list_order:
                        route, geo, shopper_profile = strategy_trip(store, st, orders[lt], cfg)
                    else:
                        if sid not in cache:
                            cache[sid] = strategy_trip(store, st, canon, cfg)
                        route, geo, shopper_profile = cache[sid]
                    m = timing(route, geo.legs, cfg.speed_mps, cfg.pick_time_s, cfg, geo.item_remaining_m)
                    rows.append({"layout": store.name, "list_size": size, "list_type": lt, "trial": t,
                                 "strategy": sid, "shopper_profile": shopper_profile or "",
                                 "n_items": len(route), "dist_m": sum(geo.legs),
                                 "aisle_reentries": geo.aisle_reentries, "retraced_m": geo.retraced_m, **m})
                    if lt == "random":
                        visits[(sid, size)].update(geo.nodes)
                        if size == mid:
                            for sp in cfg.sens_speeds:
                                for pk in cfg.sens_picks:
                                    sm = timing(route, geo.legs, sp, pk, cfg, geo.item_remaining_m)
                                    sens.append({"layout": store.name, "list_size": size, "speed_mps": sp,
                                                 "pick_time_s": pk, "trial": t, "strategy": sid,
                                                 "total_min": sm["total_min"],
                                                 "cold_mean_min": sm["cold_mean_min"]})
    return pd.DataFrame(rows), pd.DataFrame(sens), visits


METRICS = ["dist_m", "walk_min", "total_min", "cold_mean_min", "frozen_mean_min", "aisle_reentries", "retraced_m"]


def _q25(s): return s.quantile(0.25)
def _q75(s): return s.quantile(0.75)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(["layout", "list_size", "list_type", "strategy"])
    out = g[METRICS].agg(["median", _q25, _q75])
    out.columns = [f"{m}_{s.replace('_q', 'q')}" for m, s in out.columns]
    return out.reset_index()


def representative_trip(store: Store, catalog: list[Product], cfg: Config, strategies) -> tuple[str, dict]:
    """Build one reproducible medium-sized trip that illustrates the strategy differences."""
    by_cat = defaultdict(list)
    for p in catalog:
        by_cat[p.cat].append(p)
    mix = {c: w for c, w in cfg.category_mix.items() if c in by_cat}
    size = cfg.list_sizes[len(cfg.list_sizes) // 2]
    selected_attempt = 0
    for attempt in range(1000):
        rng = random.Random(f"representative|{cfg.seed}|{size}|{attempt}")
        candidate_items = sample_items(by_cat, mix, size, rng)
        candidate_written = order_list(candidate_items, "random", rng)
        candidate_canonical = sorted(candidate_items, key=lambda p: p.pid)
        if "S3" not in strategies or "S4" not in strategies:
            selected_attempt = attempt
            break
        s3_route = strategies["S3"].fn(store, candidate_written, cfg)
        s4_route = strategies["S4"].fn(store, candidate_canonical, cfg)
        s3_geo = geometry(store, s3_route, fixed_walk=True, cfg=cfg)
        s4_geo = geometry(store, s4_route, staple_dfs=True, cfg=cfg)
        if s3_geo.nodes != s4_geo.nodes:
            selected_attempt = attempt
            break
    else:
        raise ValueError("Could not find a representative trip where S3 and S4 differ")
    items, written, canonical = candidate_items, candidate_written, candidate_canonical
    results = {}
    lines = [f"REPRESENTATIVE TRIP: {store.name}, {size} items, seed {cfg.seed}, selection {selected_attempt}",
             "All values are simulated. The same shopping list is used for every strategy.\n",
             "Shopping list (written order):"]
    lines.append("  " + ", ".join(f"{p.pid}:{p.cat}@{p.node}{'*' if p.staple else ''}" for p in written))
    for sid, st in strategies.items():
        source = written if st.uses_list_order else canonical
        route, geo, shopper_profile = strategy_trip(store, st, source, cfg)
        metrics = timing(route, geo.legs, cfg.speed_mps, cfg.pick_time_s, cfg, geo.item_remaining_m)
        results[sid] = {
            "label": st.label,
            "item_order": [{"pid": p.pid, "category": p.cat, "node": p.node, "staple": p.staple} for p in route],
            "physical_route": geo.nodes,
            "distance_m": round(sum(geo.legs), 2),
            "walk_min": round(metrics["walk_min"], 2),
            "total_min": round(metrics["total_min"], 2),
            "cold_mean_min": round(metrics["cold_mean_min"], 2),
            "frozen_mean_min": round(metrics["frozen_mean_min"], 2),
            "aisle_reentries": geo.aisle_reentries,
            "retraced_m": round(geo.retraced_m, 2),
        }
        if shopper_profile is not None:
            results[sid]["shopper_profile"] = shopper_profile
        r = results[sid]
        lines.extend([
            f"\n{sid} - {st.label}",
            *([f"  sampled shopper profile: {shopper_profile}"] if shopper_profile else []),
            f"  pickup order: " + ", ".join(f"{p.pid}:{p.cat}@{p.node}" for p in route),
            f"  distance: {r['distance_m']:.1f} m | walking: {r['walk_min']:.2f} min | total: {r['total_min']:.2f} min",
            f"  cold exposure: {r['cold_mean_min']:.2f} min | frozen exposure: {r['frozen_mean_min']:.2f} min",
            f"  aisle re-entries: {r['aisle_reentries']} | retraced: {r['retraced_m']:.1f} m",
            "  physical route: " + " -> ".join(geo.nodes),
        ])
    return "\n".join(lines), {"store": store.name, "list_size": size, "seed": cfg.seed,
                              "selection_attempt": selected_attempt,
                              "shopping_list": [{"pid": p.pid, "category": p.cat, "node": p.node,
                                                  "staple": p.staple} for p in written],
                              "strategies": results}


def paired_change(df: pd.DataFrame, a: str, b: str, col="dist_m") -> pd.Series:
    """Median per-list percentage change of strategy a relative to strategy b."""
    p = df[df.list_type == "random"].pivot_table(index=["list_size", "trial"], columns="strategy", values=col)
    return ((p[a] - p[b]) / p[b] * 100).groupby("list_size").median()


def make_report(store: Store, cfg: Config, df: pd.DataFrame, sens: pd.DataFrame, strategies) -> str:
    L = []
    L.append(f"STORE: {store.name}  ({store.G.number_of_nodes()} nodes, {store.G.number_of_edges()} edges, "
             f"{len(store.checkouts)} checkout node(s))")
    L.append(f"lists per size: {cfg.n_lists}, seed: {cfg.seed}, speed: {cfg.speed_mps} m/s, "
             f"pick time: {cfg.pick_time_s} s")
    L.append(f"group order for S2: {cfg.group_order}")
    if cfg.staple_ids is not None:
        L.append(f"S4 staples: explicit product IDs {cfg.staple_ids}")
    else:
        L.append(f"S4 synthetic staples: about {cfg.staple_fraction:.0%} of {cfg.staple_categories} products")
    L.append(f"S5 targets {cfg.s5_travel_deviation_share:.0%} travel deviation beyond its shortest-tour purchase order.")
    L.append("This S5 calibration uses the mean path-decomposition shares reported in SSRN 942570; "
             "its aisle excursions are a simulation, not a directly estimated individual shopper model.")
    L.append(f"S6 profile scenario weights: {cfg.s6_profile_weights}.")
    L.append(f"S6 maximum extra distance for one aisle detour: {cfg.s6_max_detour_m:.1f} m.")
    L.append("S6 samples a shopper profile and makes probabilistic item choices using distance, list order, "
             "category continuity, and staple familiarity; browsing detours become less likely over the trip.")
    L.append("S6 profile weights and behaviour strengths are scenario assumptions, not estimates from the cited studies. "
             "Incidental purchases and shelf-level consideration are not modelled.")
    L.append("All values are simulated. Category mix, walking speed, pick time, and shopper assumptions are scenario inputs.\n")

    rnd = df[df.list_type == "random"]
    for col, title in [("dist_m", "Walking distance (m), median"),
                       ("total_min", "Total trip time (min), median"),
                       ("cold_mean_min", "Mean minutes in cart, fresh + frozen items, median"),
                       ("frozen_mean_min", "Mean minutes in cart, frozen items, median"),
                       ("aisle_reentries", "Aisle re-entries, median"),
                       ("retraced_m", "Retraced distance (m), median")]:
        t = rnd.pivot_table(index="strategy", columns="list_size", values=col, aggfunc="median").round(2)
        L.append(title + "  [list type: random]")
        L.append(t.to_string() + "\n")

    L.append("Paired change in walking distance, median over lists (negative = shorter walk)")
    ids = list(strategies)
    if all(k in ids for k in ("S1", "S2", "S3", "S0")):
        changes = {
            "S2 vs S1 (cost of the category rule)": paired_change(df, "S2", "S1"),
            "S2 vs S3 (vs habitual shopper)": paired_change(df, "S2", "S3"),
            "S2 vs S0 (vs plain list app)": paired_change(df, "S2", "S0"),
        }
        if "S4" in ids:
            changes.update({
                "S4 vs S1 (staple + aisle-search cost)": paired_change(df, "S4", "S1"),
                "S4 vs S3 (staple + aisle search vs habitual)": paired_change(df, "S4", "S3"),
            })
        if "S5" in ids:
            changes.update({
                "S5 vs S1 (empirical travel-deviation excursions)": paired_change(df, "S5", "S1"),
                "S5 vs S3 (excursions vs habitual)": paired_change(df, "S5", "S3"),
            })
        if "S6" in ids:
            changes["S6 vs S1 (literature-informed stochastic shopper)"] = paired_change(df, "S6", "S1")
        tab = pd.DataFrame(changes).round(1)
        L.append(tab.to_string() + " (%)\n")

    if not sens.empty:
        combos, rankings = [], []
        for (sp, pk), g in sens.groupby(["speed_mps", "pick_time_s"]):
            med = g.groupby("strategy")["total_min"].median().sort_values()
            rankings.append(tuple(med.index))
            combos.append((sp, pk))
        common = Counter(rankings).most_common(1)[0]
        speeds = sorted({float(c[0]) for c in combos})
        pick_times = sorted({int(c[1]) for c in combos})
        L.append(f"Sensitivity (list size {sens.list_size.iloc[0]}): {len(combos)} combinations of walking speed "
                 f"{speeds} m/s and pick time {pick_times} s.")
        L.append(f"Ranking by total time, fastest first, is {' < '.join(common[0])} in {common[1]} of {len(combos)}.")
        if common[1] < len(combos):
            L.append("Ranking changes in other combinations; see sensitivity.csv.")
    return "\n".join(L)


# --------------------------------------------------------------------------- plots
def plot_layout(store: Store, path: Path):
    cats = sorted(store.categories())
    cmap = plt.get_cmap("tab10")
    color = {c: cmap(i % 10) for i, c in enumerate(cats)}
    fig, ax = plt.subplots(figsize=(7, 6), constrained_layout=True)
    segs = [[(store.G.nodes[u]["x"], store.G.nodes[u]["y"]), (store.G.nodes[v]["x"], store.G.nodes[v]["y"])]
            for u, v in store.G.edges]
    ax.add_collection(LineCollection(segs, colors="#cccccc", linewidths=1))
    for n, d in store.G.nodes(data=True):
        first = sorted(d["stocks"])[0] if d["stocks"] else None
        ax.scatter(d["x"], d["y"], s=30, color=color.get(first, "#999999"), zorder=3)
    for c in cats:
        ax.scatter([], [], color=color[c], label=c)
    e = store.G.nodes[store.entrance]
    ax.scatter(e["x"], e["y"], marker="^", s=120, color="black", zorder=4, label="entrance")
    for c in store.checkouts:
        d = store.G.nodes[c]
        ax.scatter(d["x"], d["y"], marker="s", s=100, color="black", zorder=4)
    ax.scatter([], [], marker="s", color="black", label="checkout")
    ax.set_title(f"{store.name}  (node color = first category stocked; nodes can stock several)")
    ax.set_aspect("equal")
    ax.legend(fontsize=8, loc="center left", bbox_to_anchor=(1.0, 0.5))
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_distance_bars(summ: pd.DataFrame, strategies, path: Path):
    lts = sorted(summ.list_type.unique())
    sizes = sorted(summ.list_size.unique())
    fig, axes = plt.subplots(1, len(lts), figsize=(6.4 * len(lts), 6), sharey=True, squeeze=False,
                             constrained_layout=True)
    w = 0.8 / len(strategies)
    for ax, lt in zip(axes[0], lts):
        for k, (sid, st) in enumerate(strategies.items()):
            sub = summ[(summ.list_type == lt) & (summ.strategy == sid)].set_index("list_size").reindex(sizes)
            med = sub["dist_m_median"]
            err = [(med - sub["dist_m_q25"]).clip(lower=0), (sub["dist_m_q75"] - med).clip(lower=0)]
            ax.bar(np.arange(len(sizes)) + k * w - 0.4 + w / 2, med, w, yerr=err, capsize=2,
                   label=sid)
        ax.set_xticks(range(len(sizes)))
        ax.set_xticklabels([f"{s} items" for s in sizes])
        ax.set_title(f"List type: {lt}", fontsize=15)
        ax.set_ylabel("Walking distance (m), median with IQR", fontsize=13)
        ax.set_xlabel("Shopping-list size", fontsize=13)
        ax.tick_params(axis="both", labelsize=12)
    handles, _ = axes[0][0].get_legend_handles_labels()
    legend_labels = [
        "S0 List order", "S1 Shortest heuristic", "S2 Category route", "S3 Fixed walk",
        "S4 Staple search", "S5 Calibrated detours", "S6 Shopper profiles",
    ]
    fig.legend(handles, legend_labels, loc="outside lower center", ncol=4, frameon=False, fontsize=11)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_tradeoff(summ: pd.DataFrame, strategies, path: Path):
    sub = summ[summ.list_type == "random"]
    sizes = sorted(sub.list_size.unique())
    ncols = 2
    nrows = (len(sizes) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(12.8, 4.75 * nrows),
                             squeeze=False, constrained_layout=True)
    palette = {"S0": "#d55e00", "S1": "#0072b2", "S2": "#009e73", "S3": "#cc79a7",
               "S4": "#e69f00", "S5": "#56b4e9", "S6": "#6a3d9a"}
    label_offsets = {"S0": (7, 6), "S1": (7, 8), "S2": (7, 6), "S3": (7, -12),
                     "S4": (7, 6), "S5": (7, 6), "S6": (7, -10)}
    for ax, size in zip(axes.ravel(), sizes):
        panel = sub[sub.list_size == size].set_index("strategy")
        for sid, st in strategies.items():
            if sid not in panel.index:
                continue
            row = panel.loc[sid]
            if pd.isna(row["cold_mean_min_median"]):
                continue
            x = row["dist_m_median"]
            y = row["cold_mean_min_median"]
            xerr = [[max(0, x - row["dist_m_q25"])], [max(0, row["dist_m_q75"] - x)]]
            yerr = [[max(0, y - row["cold_mean_min_q25"])],
                    [max(0, row["cold_mean_min_q75"] - y)]]
            color = palette.get(sid, "#555555")
            ax.errorbar(x, y, xerr=xerr, yerr=yerr, fmt="o", color=color,
                        ecolor=color, elinewidth=1.2, capsize=3, markersize=7, zorder=3)
            ax.annotate(sid, (x, y), xytext=label_offsets.get(sid, (7, 6)), textcoords="offset points",
                        color=color, fontsize=11, fontweight="bold")
        ax.set_title(f"{size} items", fontsize=14)
        ax.grid(True, color="#e5e5e5", linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(axis="both", labelsize=11)
        valid = panel.dropna(subset=["dist_m_median", "cold_mean_min_median"])
        if not valid.empty:
            x_low, x_high = valid["dist_m_q25"].min(), valid["dist_m_q75"].max()
            y_low, y_high = valid["cold_mean_min_q25"].min(), valid["cold_mean_min_q75"].max()
            x_pad = max((x_high - x_low) * 0.15, 10)
            y_pad = max((y_high - y_low) * 0.18, 0.25)
            ax.set_xlim(max(0, x_low - x_pad), x_high + x_pad)
            ax.set_ylim(max(0, y_low - y_pad), y_high + y_pad)
    for index, ax in enumerate(axes.ravel()):
        if index >= len(sizes):
            ax.set_axis_off()
            continue
        if index % ncols == 0:
            ax.set_ylabel("Cold items' mean time in cart (min)", fontsize=13)
        ax.set_xlabel("Walking distance (m)", fontsize=13)
    short_labels = {
        "S0": "S0 List order", "S1": "S1 Shortest heuristic", "S2": "S2 Category route",
        "S3": "S3 Fixed walk", "S4": "S4 Staple search", "S5": "S5 Calibrated detours",
        "S6": "S6 Shopper profiles",
    }
    handles = [plt.Line2D([], [], color=palette.get(sid, "#555555"), marker="o", linestyle="none",
                          label=short_labels[sid]) for sid in strategies]
    fig.legend(handles=handles, loc="outside lower center", ncol=4, frameon=False, fontsize=10)
    fig.suptitle("Route distance vs cold exposure | random lists, medians and IQR\nEach list size uses its own axis scale",
                 fontsize=16)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_heatmaps(store: Store, visits, strategies, size, n_lists, path: Path):
    ids = [s for s in strategies if (s, size) in visits]
    if not ids:
        return
    nodes = list(store.G.nodes)
    xs = [store.G.nodes[n]["x"] for n in nodes]
    ys = [store.G.nodes[n]["y"] for n in nodes]
    segs = [[(store.G.nodes[u]["x"], store.G.nodes[u]["y"]), (store.G.nodes[v]["x"], store.G.nodes[v]["y"])]
            for u, v in store.G.edges]
    vmax = max(max(visits[(s, size)].values()) for s in ids) / n_lists
    fig, axes = plt.subplots(1, len(ids), figsize=(4.2 * len(ids) + 1, 5), squeeze=False, constrained_layout=True)
    sc = None
    for ax, sid in zip(axes[0], ids):
        ax.add_collection(LineCollection(segs, colors="#dddddd", linewidths=1))
        vals = [visits[(sid, size)].get(n, 0) / n_lists for n in nodes]
        sc = ax.scatter(xs, ys, c=vals, s=35, cmap="YlOrRd", vmin=0, vmax=vmax, zorder=3)
        ax.set_title(f"{sid} · {size} items")
        ax.set_aspect("equal")
    fig.colorbar(sc, ax=axes.ravel().tolist(), label="node visits per trip")
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_representative_trips(store: Store, representative_data: dict, path: Path):
    """Plot the physical representative route for each strategy on the store map."""
    strategies = representative_data["strategies"]
    colors = {"S0": "#d95f02", "S1": "#1b9e77", "S2": "#7570b3", "S3": "#e7298a",
              "S4": "#66a61e", "S5": "#1f78b4", "S6": "#6a3d9a"}
    fig, axes = plt.subplots(2, 4, figsize=(16, 9), sharex=True, sharey=True, constrained_layout=True)
    axes = axes.ravel()
    base_segments = [[(store.G.nodes[u]["x"], store.G.nodes[u]["y"]),
                      (store.G.nodes[v]["x"], store.G.nodes[v]["y"])] for u, v in store.G.edges]
    for ax, (sid, result) in zip(axes, strategies.items()):
        ax.add_collection(LineCollection(base_segments, colors="#dddddd", linewidths=1, zorder=1))
        route = result["physical_route"]
        points = [(store.G.nodes[n]["x"], store.G.nodes[n]["y"]) for n in route]
        x, y = zip(*points)
        color = colors.get(sid, "#333333")
        ax.plot(x, y, color=color, linewidth=2.2, alpha=0.9, zorder=2)
        ax.scatter(x, y, color=color, s=12, zorder=3)
        item_nodes = [item["node"] for item in result["item_order"]]
        pickup_points = [(store.G.nodes[n]["x"], store.G.nodes[n]["y"]) for n in item_nodes]
        px, py = zip(*pickup_points)
        ax.scatter(px, py, facecolors="white", edgecolors=color, s=42, linewidths=1.2, zorder=4)
        for number, (px_i, py_i) in enumerate(zip(px, py), start=1):
            ax.text(px_i, py_i, str(number), fontsize=8, ha="center", va="center", zorder=5)
        entrance = store.G.nodes[store.entrance]
        ax.scatter(entrance["x"], entrance["y"], marker="^", s=100, color="black", zorder=6)
        for checkout in store.checkouts:
            checkout_node = store.G.nodes[checkout]
            ax.scatter(checkout_node["x"], checkout_node["y"], marker="s", s=70, color="black", zorder=6)
        ax.set_title(f"{sid} · {result['distance_m']:.0f} m, {result['total_min']:.1f} min", fontsize=12)
        ax.set_aspect("equal")
        ax.set_xlabel("x (m)", fontsize=10)
        ax.tick_params(axis="both", labelsize=9)
    for ax in axes[len(strategies):]:
        ax.set_axis_off()
    axes[0].set_ylabel("y (m)", fontsize=10)
    axes[4].set_ylabel("y (m)", fontsize=10)
    fig.suptitle("Representative trip routes by strategy (simulated)", fontsize=16)
    fig.savefig(path, dpi=180)
    plt.close(fig)


# --------------------------------------------------------------------------- driver
def run_and_report(store: Store, catalog, cfg: Config, outroot: Path, strategies=None):
    strategies = strategies or STRATEGIES
    out = outroot / store.name
    out.mkdir(parents=True, exist_ok=True)
    df, sens, visits = run_experiment(store, catalog, cfg, strategies)
    summ = summarize(df)
    df.to_csv(out / "trials.csv", index=False)
    summ.to_csv(out / "summary.csv", index=False)
    sens.to_csv(out / "sensitivity.csv", index=False)
    representative_text, representative_data = representative_trip(store, catalog, cfg, strategies)
    (out / "representative_trip.txt").write_text(representative_text, encoding="utf-8")
    (out / "representative_trip.json").write_text(json.dumps(representative_data, indent=2), encoding="utf-8")
    plot_representative_trips(store, representative_data, out / "representative_trips.png")
    report = make_report(store, cfg, df, sens, strategies)
    (out / "report.txt").write_text(report, encoding="utf-8")
    plot_layout(store, out / "layout.png")
    plot_distance_bars(summ, strategies, out / "distance_by_strategy.png")
    plot_tradeoff(summ, strategies, out / "tradeoff_distance_vs_cold.png")
    plot_heatmaps(store, visits, strategies, cfg.list_sizes[len(cfg.list_sizes) // 2], cfg.n_lists,
                  out / "heatmaps.png")
    print(report)
    print(f"\nFiles written to {out}/")
    return df, summ


def build_parser():
    p = argparse.ArgumentParser(description="Supermarket routing simulator", formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--store", help="JSON file describing a store (overrides the grid options)")
    p.add_argument("--catalog", help="CSV with columns product,category,node")
    p.add_argument("--config", help="JSON file with Config fields")
    p.add_argument("--export-store", help="write the grid store as JSON to this path and exit")
    p.add_argument("--aisles", type=int, default=10)
    p.add_argument("--aisle-length", type=float, default=30.0, help="meters")
    p.add_argument("--aisle-spacing", type=float, default=3.0, help="meters between aisles")
    p.add_argument("--stop-step", type=float, default=3.0, help="meters between shelf stops")
    p.add_argument("--checkouts", type=int, default=1, help="number of checkout nodes, front-right")
    p.add_argument("--zones", default="default", help=f"preset {list(ZONE_PRESETS)} or a JSON zone-map file")
    p.add_argument("--sweep-zones", action="store_true", help="run every built-in zone preset on the grid")
    p.add_argument("--n-lists", type=int, help="lists per size (default 100)")
    p.add_argument("--seed", type=int)
    p.add_argument("--out", default="sim_out")
    return p


def main():
    args = build_parser().parse_args()
    cfg = load_config(args.config)
    if args.n_lists:
        cfg.n_lists = args.n_lists
    if args.seed is not None:
        cfg.seed = args.seed
    grid = dict(aisles=args.aisles, aisle_length=args.aisle_length, aisle_spacing=args.aisle_spacing,
                stop_step=args.stop_step, n_checkouts=args.checkouts)

    if args.export_store:
        s = make_grid_store(zones=load_zones(args.zones), **grid)
        Path(args.export_store).write_text(json.dumps(s.to_dict(), indent=1))
        print(f"Wrote {args.export_store}. Edit nodes, edges, entrance, checkouts, and stocks, then run with --store.")
        return

    outroot = Path(args.out)
    if args.store:
        stores = [Store.from_dict(json.loads(Path(args.store).read_text()))]
    elif args.sweep_zones:
        stores = [make_grid_store(zones=z, **grid) for z in ZONE_PRESETS]
    else:
        stores = [make_grid_store(zones=load_zones(args.zones), **grid)]

    all_summ = []
    for store in stores:
        rng = random.Random(f"catalog|{cfg.seed}")
        catalog = load_catalog_csv(args.catalog, store) if args.catalog else make_catalog(store, cfg, rng)
        _, summ = run_and_report(store, catalog, cfg, outroot)
        all_summ.append(summ)
    if len(stores) > 1:
        pd.concat(all_summ).to_csv(outroot / "sweep_summary.csv", index=False)
        print(f"\nCombined summary: {outroot / 'sweep_summary.csv'}")


if __name__ == "__main__":
    main()
