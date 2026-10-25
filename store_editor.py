#!/usr/bin/env python3
"""Graphical editor for supermarket store JSON files used by store_sim.py."""
from __future__ import annotations

import json
import math
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from store_sim import Store

ROOT = Path(__file__).resolve().parent
SIMULATOR = ROOT / "store_sim.py"
CATEGORIES = ["dry", "beverage", "fresh", "frozen", "heavy"]
CATEGORY_COLORS = {
    "dry": "#d98942",
    "beverage": "#4387a8",
    "fresh": "#4e9a69",
    "frozen": "#6888bf",
    "heavy": "#aa735b",
    "empty": "#92999d",
}


class StoreEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Supermarket Store Editor")
        self.geometry("1360x850")
        self.minsize(1050, 680)
        self.configure(background="#edf0ed")
        self.store_data = None
        self.file_path: Path | None = None
        self.selected_node: str | None = None
        self.edge_mode = False
        self.edge_start: str | None = None
        self.canvas_positions = {}
        self.node_items = {}
        self.category_vars = {cat: tk.BooleanVar(value=False) for cat in CATEGORIES}
        self.trial_var = tk.StringVar(value="1000")
        self.status_var = tk.StringVar(value="Open a store file or create a new layout.")
        self._build_ui()
        self.bind("<Configure>", self._on_resize)
        self.new_grid(aisles=10, length=30, spacing=3)
        default_store = ROOT / "my_store.json"
        if not default_store.exists():
            default_store = ROOT / "lidl_store_layout.json"
        if default_store.exists():
            try:
                self._load_store(default_store)
            except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as error:
                self.status_var.set(f"Created a blank grid; could not open my_store.json: {error}")

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background="#edf0ed")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Title.TLabel", background="#ffffff", foreground="#172a31",
                        font=("Segoe UI", 17, "bold"))
        style.configure("Section.TLabel", background="#ffffff", foreground="#34474d",
                        font=("Segoe UI", 10, "bold"))
        style.configure("Hint.TLabel", background="#ffffff", foreground="#68777b",
                        font=("Segoe UI", 9))
        style.configure("TButton", padding=(9, 6))
        style.configure("Accent.TButton", padding=(10, 7), font=("Segoe UI", 9, "bold"))

        toolbar = ttk.Frame(self, style="App.TFrame", padding=(14, 12, 14, 8))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="New layout", command=self.new_dialog).pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="Open", command=self.open_dialog).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Save", command=self.save).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Save as...", command=self.save_as).pack(side="left", padx=6)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Button(toolbar, text="Add node", command=self.add_node_dialog).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Connect nodes", command=self.toggle_edge_mode).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Remove edge", command=self.remove_edge_mode).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Run simulation", style="Accent.TButton", command=self.run_simulation).pack(
            side="right", padx=(8, 0))
        ttk.Label(toolbar, text="Lists per size", background="#edf0ed", foreground="#526268").pack(side="right")
        ttk.Entry(toolbar, textvariable=self.trial_var, width=7).pack(side="right", padx=(8, 5))

        body = ttk.Frame(self, style="App.TFrame", padding=(14, 4, 14, 10))
        body.pack(fill="both", expand=True)
        panel = ttk.Frame(body, style="Panel.TFrame", padding=18, width=280)
        panel.pack(side="left", fill="y", padx=(0, 12))
        panel.pack_propagate(False)
        ttk.Label(panel, text="Store layout", style="Title.TLabel").pack(anchor="w")
        ttk.Label(panel, text="Select a map point to edit its stocked categories.",
                  style="Hint.TLabel", wraplength=245).pack(anchor="w", pady=(5, 16))

        ttk.Label(panel, text="STORE NAME", style="Section.TLabel").pack(anchor="w")
        self.name_var = tk.StringVar(value="my_store")
        ttk.Entry(panel, textvariable=self.name_var).pack(fill="x", pady=(5, 13))

        ttk.Separator(panel).pack(fill="x", pady=7)
        ttk.Label(panel, text="SELECTED LOCATION", style="Section.TLabel").pack(anchor="w", pady=(3, 5))
        self.selected_var = tk.StringVar(value="No location selected")
        ttk.Label(panel, textvariable=self.selected_var, style="Hint.TLabel", wraplength=240).pack(anchor="w")
        ttk.Label(panel, text="Aisle label (blank for open area)", style="Hint.TLabel").pack(anchor="w", pady=(9, 3))
        self.aisle_var = tk.StringVar()
        self.aisle_entry = ttk.Entry(panel, textvariable=self.aisle_var)
        self.aisle_entry.pack(fill="x")
        self.aisle_entry.bind("<FocusOut>", lambda _event: self.apply_aisle())
        ttk.Button(panel, text="Apply aisle label", command=self.apply_aisle).pack(fill="x", pady=(5, 9))

        ttk.Label(panel, text="STOCKED CATEGORIES", style="Section.TLabel").pack(anchor="w", pady=(6, 4))
        for cat in CATEGORIES:
            row = ttk.Frame(panel, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            tk.Label(row, text="  ", background=CATEGORY_COLORS[cat], width=2).pack(side="left", padx=(0, 8))
            ttk.Checkbutton(row, text=cat.title(), variable=self.category_vars[cat],
                            command=self.apply_categories).pack(side="left")

        ttk.Separator(panel).pack(fill="x", pady=10)
        ttk.Button(panel, text="Set as entrance", command=self.set_entrance).pack(fill="x", pady=3)
        ttk.Button(panel, text="Toggle checkout", command=self.toggle_checkout).pack(fill="x", pady=3)
        ttk.Button(panel, text="Delete selected node", command=self.delete_node).pack(fill="x", pady=3)
        ttk.Label(panel, text="Tip: click two map points after choosing Connect nodes.\nNew edges use the points' map distance.",
                  style="Hint.TLabel", wraplength=240, justify="left").pack(anchor="w", pady=(12, 0))

        map_panel = ttk.Frame(body, style="Panel.TFrame", padding=10)
        map_panel.pack(side="left", fill="both", expand=True)
        self.map_title = ttk.Label(map_panel, text="Map", style="Section.TLabel")
        self.map_title.pack(anchor="w", padx=8, pady=(3, 8))
        self.canvas = tk.Canvas(map_panel, background="#fbfcfa", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self.on_canvas_click)

        footer = ttk.Frame(self, style="App.TFrame", padding=(16, 0, 16, 10))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var, background="#edf0ed", foreground="#43535a").pack(anchor="w")

    def new_dialog(self):
        dialog = tk.Toplevel(self)
        dialog.title("Create grid layout")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        fields = [("Parallel aisles", "10"), ("Aisle length (m)", "30"), ("Aisle spacing (m)", "3")]
        values = {}
        for row, (label, default) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 15))
            var = tk.StringVar(value=default)
            ttk.Entry(frame, textvariable=var, width=12).grid(row=row, column=1, sticky="ew", pady=5)
            values[label] = var
        def create():
            try:
                aisles = int(values["Parallel aisles"].get())
                length = float(values["Aisle length (m)"].get())
                spacing = float(values["Aisle spacing (m)"].get())
                if aisles < 2 or length < 6 or spacing <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid dimensions", "Use at least 2 aisles, a length of 6 m or more, and positive spacing.", parent=dialog)
                return
            self.new_grid(aisles, length, spacing)
            dialog.destroy()
        ttk.Button(frame, text="Create", command=create).grid(row=len(fields), column=1, sticky="e", pady=(12, 0))

    def new_grid(self, aisles: int, length: float, spacing: float):
        step = 3.0
        rows = max(3, round(length / step) + 1)
        nodes = {}
        edges = []
        for aisle in range(aisles):
            for row in range(rows):
                node = f"A{aisle}R{row}"
                nodes[node] = {"x": aisle * spacing, "y": row * step,
                               "aisle": None if row in (0, rows - 1) else f"A{aisle + 1}", "stocks": []}
                if row:
                    edges.append([f"A{aisle}R{row - 1}", node, step])
        for aisle in range(aisles - 1):
            edges.append([f"A{aisle}R0", f"A{aisle + 1}R0", spacing])
            edges.append([f"A{aisle}R{rows - 1}", f"A{aisle + 1}R{rows - 1}", spacing])
        entrance = "A0R0"
        self.store_data = {"name": "my_store", "entrance": entrance,
                           "checkouts": [f"A{aisles - 1}R0"], "walk": [],
                           "nodes": nodes, "edges": edges}
        self.name_var.set("my_store")
        self.file_path = None
        self.selected_node = None
        self.edge_mode = False
        self.edge_start = None
        self._refresh()
        self.status_var.set(f"Blank {aisles}-aisle grid created. Select shelf locations and assign categories.")

    def open_dialog(self):
        filename = filedialog.askopenfilename(title="Open store layout", initialdir=ROOT,
                                              filetypes=[("Store layout", "*.json"), ("All files", "*.*")])
        if not filename:
            return
        try:
            self._load_store(Path(filename))
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as error:
            messagebox.showerror("Could not open store", str(error))
            return

    def _load_store(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self._validate_data(data)
        self.store_data = data
        self.file_path = path
        self.name_var.set(data.get("name", "my_store"))
        self.selected_node = None
        self.edge_mode = False
        self.edge_start = None
        self._refresh()
        self.status_var.set(f"Opened {path.name}")

    def _validate_data(self, data):
        if not isinstance(data.get("nodes"), dict) or not data["nodes"]:
            raise ValueError("Store must contain at least one node.")
        if not isinstance(data.get("edges"), list):
            raise ValueError("Store edges must be a list.")
        for edge in data["edges"]:
            if len(edge) < 2 or edge[0] not in data["nodes"] or edge[1] not in data["nodes"]:
                raise ValueError(f"Invalid edge: {edge}")
        Store.from_dict(data)

    def _store_to_save(self):
        data = {"name": self.name_var.get().strip() or "my_store",
                "entrance": self.store_data["entrance"],
                "checkouts": list(self.store_data["checkouts"]),
                "nodes": self.store_data["nodes"],
                "edges": self.store_data["edges"]}
        adjacency = {node: set() for node in data["nodes"]}
        for edge in data["edges"]:
            if len(edge) < 2 or edge[0] not in adjacency or edge[1] not in adjacency:
                raise ValueError(f"Invalid edge: {edge}")
            adjacency[edge[0]].add(edge[1])
            adjacency[edge[1]].add(edge[0])
        entrance = data["entrance"]
        if entrance not in adjacency:
            raise ValueError("Choose an entrance that exists on the map.")
        # S3's habitual route is a continuous depth-first walk over all map locations.
        walk = [entrance]
        visited = {entrance}
        def traverse(node):
            for nxt in sorted(adjacency[node]):
                if nxt in visited:
                    continue
                visited.add(nxt)
                walk.append(nxt)
                traverse(nxt)
                walk.append(node)
        traverse(entrance)
        if len(visited) != len(adjacency):
            raise ValueError("Store map is disconnected. Connect all locations before saving.")
        data["walk"] = walk
        Store.from_dict(data)
        return data

    def save(self):
        if self.file_path is None:
            return self.save_as()
        try:
            data = self._store_to_save()
            self.file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.store_data = data
            self.status_var.set(f"Saved {self.file_path.name}")
        except (OSError, ValueError, KeyError, TypeError) as error:
            messagebox.showerror("Could not save store", str(error))

    def save_as(self):
        filename = filedialog.asksaveasfilename(title="Save store layout", initialdir=ROOT,
                                                initialfile=f"{self.name_var.get() or 'my_store'}.json",
                                                defaultextension=".json",
                                                filetypes=[("Store layout", "*.json")])
        if not filename:
            return
        self.file_path = Path(filename)
        self.save()

    def run_simulation(self):
        try:
            trials = int(self.trial_var.get())
            if trials < 1:
                raise ValueError
            data = self._store_to_save()
            target = self.file_path or ROOT / f"{data['name']}.json"
            target.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.file_path = target
            output = ROOT / f"{data['name']}_results"
            subprocess.Popen([sys.executable, str(SIMULATOR), "--store", str(target),
                              "--n-lists", str(trials), "--out", str(output)], cwd=ROOT)
            self.status_var.set(f"Simulation started. Results will be saved in {output.name}.")
        except (ValueError, OSError, KeyError, TypeError) as error:
            messagebox.showerror("Could not run simulation", str(error))

    def add_node_dialog(self):
        if not self.store_data:
            return
        node = simpledialog.askstring("Add location", "Location ID (e.g. A10R2):", parent=self)
        if not node:
            return
        if node in self.store_data["nodes"]:
            messagebox.showerror("Duplicate ID", f"{node} already exists.")
            return
        x = simpledialog.askfloat("Add location", "x coordinate in metres:", parent=self)
        if x is None:
            return
        y = simpledialog.askfloat("Add location", "y coordinate in metres:", parent=self)
        if y is None:
            return
        aisle = simpledialog.askstring("Add location", "Aisle label (blank for open area):", parent=self)
        self.store_data["nodes"][node] = {"x": x, "y": y, "aisle": aisle or None, "stocks": []}
        self.selected_node = node
        self._refresh()
        self.status_var.set(f"Added {node}. Connect it to the map before saving.")

    def toggle_edge_mode(self):
        self.edge_mode = not self.edge_mode
        self.edge_start = None
        self.status_var.set("Connect mode: click the first location, then the second." if self.edge_mode
                            else "Connect mode off.")

    def remove_edge_mode(self):
        self.edge_mode = True
        self.edge_start = "__REMOVE__"
        self.status_var.set("Remove-edge mode: click the two endpoints of the edge to remove.")

    def on_canvas_click(self, event):
        node = self._node_at(event.x, event.y)
        if node is None:
            return
        if self.edge_mode:
            if self.edge_start in (None, "__REMOVE__"):
                self.edge_start = node
                if self.edge_start == node and self.status_var.get().startswith("Remove-edge"):
                    self.status_var.set(f"Select the other endpoint for edge from {node}.")
                else:
                    self.status_var.set(f"First endpoint: {node}. Click the other endpoint.")
                return
            first, second = self.edge_start, node
            remove = self.status_var.get().startswith("Select the other endpoint") or self.status_var.get().startswith("Remove-edge")
            if first == second:
                return
            edge_index = self._edge_index(first, second)
            if remove:
                if edge_index is None:
                    messagebox.showinfo("No edge", f"There is no edge between {first} and {second}.")
                else:
                    del self.store_data["edges"][edge_index]
                    self.status_var.set(f"Removed edge {first} - {second}.")
            elif edge_index is None:
                a, b = self.store_data["nodes"][first], self.store_data["nodes"][second]
                weight = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
                self.store_data["edges"].append([first, second, round(weight, 3)])
                self.status_var.set(f"Connected {first} to {second} ({weight:g} m).")
            else:
                self.status_var.set(f"{first} and {second} are already connected.")
            self.edge_mode = False
            self.edge_start = None
            self._refresh()
            return
        self.select_node(node)

    def _edge_index(self, first, second):
        for index, edge in enumerate(self.store_data["edges"]):
            if {edge[0], edge[1]} == {first, second}:
                return index
        return None

    def _node_at(self, x, y):
        if not self.canvas_positions:
            return None
        node, distance = min(((node, math.hypot(px - x, py - y))
                              for node, (px, py) in self.canvas_positions.items()), key=lambda item: item[1])
        return node if distance <= 18 else None

    def select_node(self, node):
        self.selected_node = node
        data = self.store_data["nodes"][node]
        self.selected_var.set(f"{node}   ({data['x']:g} m, {data['y']:g} m)")
        self.aisle_var.set(data.get("aisle") or "")
        for cat, var in self.category_vars.items():
            var.set(cat in data.get("stocks", []))
        self._refresh()

    def apply_categories(self):
        if self.selected_node is None:
            return
        self.store_data["nodes"][self.selected_node]["stocks"] = [
            cat for cat, var in self.category_vars.items() if var.get()]
        self._refresh()

    def apply_aisle(self):
        if self.selected_node is None:
            return
        self.store_data["nodes"][self.selected_node]["aisle"] = self.aisle_var.get().strip() or None
        self._refresh()

    def set_entrance(self):
        if self.selected_node is None:
            return
        self.store_data["entrance"] = self.selected_node
        self._refresh()
        self.status_var.set(f"{self.selected_node} is now the entrance.")

    def toggle_checkout(self):
        if self.selected_node is None:
            return
        checkouts = self.store_data["checkouts"]
        if self.selected_node in checkouts:
            if len(checkouts) == 1:
                messagebox.showinfo("Checkout required", "A store must have at least one checkout.")
                return
            checkouts.remove(self.selected_node)
        else:
            checkouts.append(self.selected_node)
        self._refresh()
        self.status_var.set(f"Updated checkout locations: {', '.join(checkouts)}")

    def delete_node(self):
        node = self.selected_node
        if node is None:
            return
        if node == self.store_data["entrance"] or node in self.store_data["checkouts"]:
            messagebox.showerror("Location in use", "Move the entrance or checkout before deleting this location.")
            return
        if not messagebox.askyesno("Delete location", f"Delete {node} and its connected edges?"):
            return
        del self.store_data["nodes"][node]
        self.store_data["edges"] = [e for e in self.store_data["edges"] if node not in e[:2]]
        self.selected_node = None
        self.selected_var.set("No location selected")
        self._refresh()

    def _on_resize(self, _event):
        if self.store_data and hasattr(self, "canvas"):
            self.after_idle(self._draw_map)

    def _draw_map(self):
        if not self.store_data:
            return
        self.canvas.delete("all")
        nodes = self.store_data["nodes"]
        if not nodes:
            return
        width = max(self.canvas.winfo_width(), 200)
        height = max(self.canvas.winfo_height(), 200)
        margin = 45
        xs = [float(node["x"]) for node in nodes.values()]
        ys = [float(node["y"]) for node in nodes.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        scale = min((width - 2 * margin) / max(max_x - min_x, 1),
                    (height - 2 * margin) / max(max_y - min_y, 1))
        map_width, map_height = (max_x - min_x) * scale, (max_y - min_y) * scale
        offset_x = (width - map_width) / 2
        offset_y = (height - map_height) / 2
        self.canvas_positions = {
            node: (offset_x + (float(data["x"]) - min_x) * scale,
                   height - offset_y - (float(data["y"]) - min_y) * scale)
            for node, data in nodes.items()
        }
        for edge in self.store_data["edges"]:
            if edge[0] not in self.canvas_positions or edge[1] not in self.canvas_positions:
                continue
            x1, y1 = self.canvas_positions[edge[0]]
            x2, y2 = self.canvas_positions[edge[1]]
            self.canvas.create_line(x1, y1, x2, y2, fill="#b8c1bf", width=2, tags=("edge",))
        for node, data in nodes.items():
            x, y = self.canvas_positions[node]
            stocks = data.get("stocks", [])
            color = CATEGORY_COLORS.get(stocks[0], CATEGORY_COLORS["empty"]) if stocks else CATEGORY_COLORS["empty"]
            selected = node == self.selected_node or node == self.edge_start
            radius = 8 if selected else 6
            outline = "#172a31" if selected else "#ffffff"
            width = 3 if selected else 1.5
            if node == self.store_data["entrance"]:
                self.canvas.create_oval(x - 10, y - 10, x + 10, y + 10, fill="#e2a331", outline="#172a31", width=2)
            elif node in self.store_data["checkouts"]:
                self.canvas.create_rectangle(x - 9, y - 9, x + 9, y + 9, fill="#253b43", outline="#ffffff", width=2)
            else:
                self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                                        fill=color, outline=outline, width=width)
            if selected or node == self.store_data["entrance"] or node in self.store_data["checkouts"]:
                self.canvas.create_text(x + 9, y - 9, text=node, anchor="sw", fill="#43535a",
                                        font=("Segoe UI", 7))
            self.node_items[node] = (x, y)
        self.map_title.configure(text=f"{self.store_data.get('name', 'Store')}  ·  {len(nodes)} locations  ·  {len(self.store_data['edges'])} paths")

    def _refresh(self):
        if not self.store_data:
            return
        self.store_data["name"] = self.name_var.get().strip() or "my_store"
        self.node_items = {}
        self._draw_map()
        if self.selected_node in self.store_data["nodes"]:
            data = self.store_data["nodes"][self.selected_node]
            self.selected_var.set(f"{self.selected_node}   ({data['x']:g} m, {data['y']:g} m)")
            for cat, var in self.category_vars.items():
                var.set(cat in data.get("stocks", []))


if __name__ == "__main__":
    app = StoreEditor()
    app.mainloop()
