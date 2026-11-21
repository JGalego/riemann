#!/usr/bin/env python3
"""Correlate build progress with resource usage and cost.

Reads what aws/stats.sh collected in aws/results/stats/ and writes, next to it:

  modules.csv      one row per built module: library, finish time, compile time,
                   .olean size, warnings
  timeseries.csv   5-minute buckets: modules built, Lean jobs running, busy cores,
                   memory, EBS throughput and queue, spot cost
  report.html      the same, as charts and tables (open it in a browser)

Standard library only (Python 3.10+). Usage: python3 aws/analyze.py [stats dir]
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import os
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATS = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "results" / "stats"
SRC = HERE.parent / "lean" / "src"
BUCKET = 300  # seconds; CloudWatch basic monitoring resolution
# 2 TB gp3 with 6000 IOPS and 500 MB/s, eu-west-1 list price (see watch.sh)
VOLUME_USD_PER_HOUR = float(os.environ.get("VOLUME_USD_PER_HOUR", "0.29"))
LIBRARY_FALLBACK = {"Development": 141928, "Formalism": 18254, "GroundTheory": 8507,
                    "ReciprocityTheory": 3845, "CompanionTheory": 2027, "PositivityTheory": 449,
                    "Lemmata": 167, "BuildAid": 5, "Supplements": 3, "Main": 1, "Statements": 1}

BUILT_RE = re.compile(r"^(\S+) \[\d+/\d+\] Built (\S+) \(([^)]*)\)\s*$")
DIAG_RE = re.compile(r"^(warning|error): src/(.+?)\.lean:\d+")
DUR_RE = re.compile(r"([\d.]+)\s*(ms|h|m|s)")


def iso_epoch(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def utc(t: float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime(fmt)


def duration_s(text: str) -> float | None:
    parts = DUR_RE.findall(text)
    if not parts:
        return None
    unit = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    return sum(float(v) * unit[u] for v, u in parts)


def bucket(t: float) -> int:
    return int(t // BUCKET * BUCKET)


def pearson(xs, ys) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    try:
        return statistics.correlation([p[0] for p in pairs], [p[1] for p in pairs])
    except statistics.StatisticsError:
        return None


def spearman(xs, ys) -> float | None:
    def ranks(v):
        order = sorted(range(len(v)), key=v.__getitem__)
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    return pearson(ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs]))


def pct(v: list[float], q: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(q * len(s)))] if s else 0.0


# --- Load --------------------------------------------------------------------

def library_totals() -> Counter:
    totals = Counter()
    if SRC.is_dir():
        for root, _, files in os.walk(SRC):
            rel = Path(root).relative_to(SRC).parts
            for f in files:
                if f.endswith(".lean"):
                    totals[rel[0] if rel else f[:-5]] += 1
    return totals or Counter(LIBRARY_FALLBACK)


def load_modules() -> dict[str, dict]:
    mods = {}
    with open(STATS / "modules.tsv") as f:
        for line in f:
            t, size, path = line.rstrip("\n").split("\t")
            name = path[:-len(".olean")].replace("/", ".")
            mods[name] = {"module": name, "library": path.split("/")[0].removesuffix(".olean"),
                          "finish": float(t), "olean_bytes": int(size),
                          "duration": None, "mark": "", "warnings": 0, "errors": 0}
    log = STATS / "build.log.gz"
    if log.exists():
        with gzip.open(log, "rt", errors="replace") as f:
            for line in f:
                if m := BUILT_RE.match(line):
                    mod = mods.get(m.group(2))
                    if mod is not None:
                        mod["duration"] = duration_s(m.group(3))
                        mod["mark"] = m.group(1)
                elif m := DIAG_RE.match(line):
                    mod = mods.get(m.group(2).replace("/", "."))
                    if mod is not None:
                        mod["warnings" if m.group(1) == "warning" else "errors"] += 1
    return mods


def load_instances() -> list[dict]:
    out = []
    p = STATS / "instances.tsv"
    if p.exists():
        for line in p.read_text().splitlines():
            iid, itype, az, launched = line.split("\t")
            out.append({"id": iid, "type": itype, "az": az, "launch": iso_epoch(launched)})
    out.sort(key=lambda i: i["launch"])
    for inst in out:
        m = re.search(r"\.(\d+)xlarge$", inst["type"])
        inst["vcpus"] = 4 * int(m.group(1)) if m else None
        machine = STATS / "sar" / inst["id"] / "machine.txt"
        if machine.exists():
            for line in machine.read_text().splitlines():
                if line.startswith("vcpus="):
                    inst["vcpus"] = int(line.split("=")[1])
    return out


def load_cloudwatch(name: str) -> dict[str, list[tuple[float, float]]]:
    series = defaultdict(list)
    p = STATS / "cloudwatch" / f"{name}.tsv"
    if p.exists():
        for line in p.read_text().splitlines():
            label, ts, val = line.split("\t")
            series[label].append((iso_epoch(ts), float(val)))
    return series


def load_sar(kind: str) -> list[dict]:
    """Rows of every sar/<instance>/saNN-<kind>.csv, with t = interval midpoint."""
    rows = []
    for p in sorted((STATS / "sar").glob(f"*/sa*-{kind}.csv")):
        header = None
        for line in p.read_text().splitlines():
            if line.startswith("# "):
                header = line[2:].split(";")
                continue
            vals = line.split(";")
            if header is None or len(vals) != len(header):
                continue  # LINUX-RESTART markers
            row = dict(zip(header, vals))
            try:
                row["t"] = float(row["timestamp"]) - float(row["interval"]) / 2
            except ValueError:
                continue
            row["instance"] = p.parent.name
            rows.append(row)
    return rows


def load_spot() -> dict[tuple[str, str], list[tuple[float, float]]]:
    prices = defaultdict(list)
    p = STATS / "spot-prices.tsv"
    if p.exists():
        for line in p.read_text().splitlines():
            itype, az, ts, price = line.split("\t")
            prices[(itype, az)].append((iso_epoch(ts), float(price)))
    for v in prices.values():
        v.sort()
    return prices


# --- Analyse -----------------------------------------------------------------

def main() -> None:
    if not (STATS / "modules.tsv").exists():
        sys.exit(f"No {STATS}/modules.tsv: run aws/stats.sh first.")
    mods = load_modules()
    instances = load_instances()
    spot = load_spot()
    fetched = (iso_epoch((STATS / "fetched_at.txt").read_text().strip())
               if (STATS / "fetched_at.txt").exists() else max(m["finish"] for m in mods.values()))
    # The snapshot is taken a few seconds after fetched_at is recorded.
    fetched = max(fetched, max(m["finish"] for m in mods.values()))
    totals = library_totals()
    total_modules = sum(totals.values())
    timed = [m for m in mods.values() if m["duration"] is not None]
    first_finish = min(m["finish"] for m in mods.values())
    last_finish = max(m["finish"] for m in mods.values())
    start = min([i["launch"] for i in instances] + [first_finish - 600])

    # Instance lifetimes: until the next launch, or until the snapshot.
    for a, b in zip(instances, instances[1:] + [None]):
        a["end"] = b["launch"] if b else fetched

    def price_at(inst, t):
        hist = spot.get((inst["type"], inst["az"]), [])
        p = None
        for ts, v in hist:
            if ts <= t:
                p = v
        return p if p is not None else (hist[0][1] if hist else 0.0)

    keys = list(range(bucket(start), bucket(fetched) + BUCKET, BUCKET))
    idx = {k: i for i, k in enumerate(keys)}
    n = len(keys)
    built = [0] * n
    busy_s = [0.0] * n          # compile seconds overlapping each bucket
    for m in mods.values():
        built[idx[bucket(m["finish"])]] += 1
        if m["duration"]:
            a, b = m["finish"] - m["duration"], m["finish"]
            k = bucket(a)
            while k <= b and k in idx:
                busy_s[idx[k]] += max(0.0, min(b, k + BUCKET) - max(a, k))
                k += BUCKET
    jobs = [s / BUCKET for s in busy_s]

    vcpus = [None] * n
    cost = [0.0] * n
    for inst in instances:
        for k in keys:
            lo, hi = max(k, inst["launch"]), min(k + BUCKET, inst["end"])
            if hi > lo:
                i = idx[k]
                vcpus[i] = inst["vcpus"]
                cost[i] += price_at(inst, lo) * (hi - lo) / 3600
    for i, k in enumerate(keys):
        lo, hi = max(k, start), min(k + BUCKET, fetched)
        if hi > lo:
            cost[i] += VOLUME_USD_PER_HOUR * (hi - lo) / 3600

    busy = [None] * n
    for inst in instances:
        for t, v in load_cloudwatch(inst["id"]).get("CPUUtilization", []):
            if bucket(t) in idx and inst["vcpus"]:
                busy[idx[bucket(t)]] = v / 100 * inst["vcpus"]

    mem_gb = [None] * n
    mem_total_gb = None
    for p in (STATS / "sar").glob("*/machine.txt"):
        if m := re.search(r"MemTotal:\s+(\d+) kB", p.read_text()):
            mem_total_gb = int(m.group(1)) / 2**20
    for r in load_sar("mem"):
        k = bucket(r["t"])
        if k in idx:
            mem_gb[idx[k]] = int(r["kbmemused"]) / 2**20
    sar_cpu = [None] * n
    for r in load_sar("cpu"):
        k = bucket(r["t"])
        if k in idx and r.get("CPU") == "-1":
            sar_cpu[idx[k]] = 100 - float(r["%idle"])
    swap_gb = [None] * n
    for r in load_sar("swap"):
        k = bucket(r["t"])
        if k in idx:
            swap_gb[idx[k]] = int(r["kbswpused"]) / 2**20

    vol = {}
    for p in (STATS / "cloudwatch").glob("vol-*.tsv"):
        vol = load_cloudwatch(p.stem)
    def per_bucket(label, agg):
        acc = defaultdict(list)
        for t, v in vol.get(label, []):
            if bucket(t) in idx:
                acc[idx[bucket(t)]].append(v)
        out = [None] * n
        for i, vs in acc.items():
            out[i] = agg(vs)
        return out
    write_mbs = [None if v is None else v / BUCKET / 1e6 for v in per_bucket("VolumeWriteBytes", sum)]
    read_mbs = [None if v is None else v / BUCKET / 1e6 for v in per_bucket("VolumeReadBytes", sum)]
    queue = per_bucket("VolumeQueueLength", statistics.fmean)

    # The bucket holding the snapshot is incomplete: leave it out of charts and correlations.
    full = n if fetched % BUCKET == 0 else n - 1
    # Build window: complete buckets between the first and last finished module.
    win = [i for i, k in enumerate(keys[:full]) if bucket(first_finish) <= k <= bucket(last_finish)]
    W = lambda xs: [xs[i] for i in win]
    corr = [
        ("Modules built vs. busy cores", "per 5 min", pearson(W(built), W(busy))),
        ("Lean jobs running vs. busy cores", "per 5 min", pearson(W(jobs), W(busy))),
        ("Modules built vs. Lean jobs running", "per 5 min", pearson(W(built), W(jobs))),
        ("Modules built vs. memory used", "per 10 min (sar)", pearson(W(built), W(mem_gb))),
        ("Modules built vs. EBS write throughput", "per 5 min", pearson(W(built), W(write_mbs))),
        ("Busy cores vs. EBS queue length", "per 5 min", pearson(W(busy), W(queue))),
    ]
    sizes = [m["olean_bytes"] for m in timed if m["duration"] > 0]
    durs = [m["duration"] for m in timed if m["duration"] > 0]
    corr.append(("Compile time vs. .olean size (log–log)", "per module",
                 pearson([math.log(s) for s in sizes], [math.log(d) for d in durs])))
    corr.append(("Compile time vs. .olean size (rank)", "per module", spearman(sizes, durs)))

    total_cost = sum(cost)
    compile_h = sum(durs) / 3600
    window_s = max(1.0, last_finish - first_finish)
    busy_w = [b for b in W(busy) if b is not None]
    last_hour = sum(1 for m in mods.values() if m["finish"] >= last_finish - 3600)
    remaining = total_modules - len(mods)

    # Libraries
    libs = defaultdict(list)
    for m in mods.values():
        libs[m["library"]].append(m)
    lib_rows = []
    for lib, total in sorted(totals.items(), key=lambda kv: -kv[1]):
        ms = libs.get(lib, [])
        d = [m["duration"] for m in ms if m["duration"] is not None]
        lib_rows.append({
            "library": lib, "built": len(ms), "total": total,
            "compile_h": sum(d) / 3600, "median_s": statistics.median(d) if d else None,
            "p95_s": pct(d, 0.95) if d else None, "max_s": max(d) if d else None,
            "warnings_per_module": (sum(m["warnings"] for m in ms) / len(ms)) if ms else None,
            "cost_share": (sum(d) / 3600 / compile_h * total_cost) if compile_h else 0.0,
        })
    slowest = sorted(timed, key=lambda m: -m["duration"])[:15]

    # Histogram of compile times, log-spaced bins
    edges = [0.1 * 2 ** (i / 2) for i in range(30)]
    hist = [0] * (len(edges) - 1)
    for d in durs:
        for j in range(len(edges) - 1):
            if edges[j] <= d < edges[j + 1]:
                hist[j] += 1
                break
    while hist and hist[-1] == 0:
        hist.pop()
    while hist and hist[0] == 0:
        hist.pop(0)
        edges.pop(0)

    rng = random.Random(0)
    sample = timed if len(timed) <= 20000 else rng.sample(timed, 20000)

    # --- Write ---------------------------------------------------------------
    with open(STATS / "modules.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["module", "library", "finish_utc", "compile_s", "olean_bytes", "warnings", "errors", "mark"])
        for m in sorted(mods.values(), key=lambda m: m["finish"]):
            w.writerow([m["module"], m["library"], utc(m["finish"], "%Y-%m-%dT%H:%M:%SZ"),
                        "" if m["duration"] is None else round(m["duration"], 3),
                        m["olean_bytes"], m["warnings"], m["errors"], m["mark"]])
    cols = {"modules_built": built, "lean_jobs": jobs, "busy_cores": busy, "vcpus": vcpus,
            "sar_cpu_pct": sar_cpu, "mem_used_gb": mem_gb, "swap_used_gb": swap_gb,
            "ebs_write_mbs": write_mbs, "ebs_read_mbs": read_mbs, "ebs_queue": queue, "cost_usd": cost}
    with open(STATS / "timeseries.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["bucket_utc"] + list(cols))
        for i, k in enumerate(keys):
            w.writerow([utc(k, "%Y-%m-%dT%H:%M:%SZ")] +
                       ["" if c[i] is None else round(c[i], 4) for c in cols.values()])

    r = lambda v, d=2: None if v is None else round(v, d)
    data = {
        "fetched": utc(fetched), "status": (STATS / "status.txt").read_text().strip()
        if (STATS / "status.txt").exists() else "?",
        "instances": [{"id": i["id"], "type": i["type"], "az": i["az"], "vcpus": i["vcpus"],
                       "from": utc(i["launch"]), "to": utc(i["end"])} for i in instances],
        "tiles": {
            "built": len(mods), "total": total_modules,
            "elapsed_h": (fetched - start) / 3600, "compile_h": compile_h,
            "jobs_mean": sum(durs) / window_s, "busy_mean": statistics.fmean(busy_w) if busy_w else None,
            "vcpus": instances[-1]["vcpus"] if instances else None,
            "cost": total_cost, "cost_per_1k": total_cost / len(mods) * 1000 if mods else None,
            "last_hour": last_hour,
            "eta_h": remaining / last_hour if last_hour else None,
            "warnings": sum(m["warnings"] for m in mods.values()),
            "errors": sum(m["errors"] for m in mods.values()),
        },
        "t": [utc(k) for k in keys[:full]],
        "built": built[:full], "jobs": [r(v) for v in jobs[:full]], "busy": [r(v) for v in busy[:full]],
        "vcpus": vcpus[:full], "mem": [r(v) for v in mem_gb[:full]], "mem_total": r(mem_total_gb),
        "write": [r(v) for v in write_mbs[:full]], "read": [r(v) for v in read_mbs[:full]],
        "queue": [r(v) for v in queue[:full]],
        "cum_cost": [r(v) for v in [sum(cost[:i + 1]) for i in range(full)]],
        "corr": [{"pair": a, "grain": g, "r": r(v)} for a, g, v in corr],
        "hist": {"lo": [r(e, 3) for e in edges[:len(hist)]], "hi": [r(e, 3) for e in edges[1:len(hist) + 1]],
                 "n": hist},
        "scatter": {"size": [m["olean_bytes"] for m in sample], "dur": [r(m["duration"], 3) for m in sample],
                    "name": [m["module"] for m in sample], "sampled": len(sample) < len(timed),
                    "count": len(timed)},
        "libs": [{k: (r(v) if isinstance(v, float) else v) for k, v in row.items()} for row in lib_rows],
        "slowest": [{"module": m["module"], "library": m["library"], "s": r(m["duration"], 1),
                     "olean_kb": round(m["olean_bytes"] / 1024), "finish": utc(m["finish"])} for m in slowest],
    }
    html = TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
    (STATS / "report.html").write_text(html)

    t = data["tiles"]
    print(f"Modules: {t['built']:,}/{t['total']:,} ({t['built'] / t['total']:.1%}), "
          f"{len(timed):,} with compile times")
    print(f"Compile time: {compile_h:,.1f} h over {window_s / 3600:.1f} h of build "
          f"-> {t['jobs_mean']:.1f} Lean jobs at once on average")
    if t["busy_mean"] is not None:
        print(f"Busy cores (CloudWatch): {t['busy_mean']:.1f} of {t['vcpus']} on average")
    print(f"Cost so far: ${total_cost:,.2f} (${t['cost_per_1k']:.3f} per 1,000 modules)")
    if t["eta_h"]:
        print(f"At the last hour's pace ({last_hour:,} modules/h): {t['eta_h']:.1f} h to go")
    for a, g, v in corr:
        print(f"  r = {'n/a' if v is None else f'{v:+.2f}'}  {a} ({g})")
    print(f"Wrote {STATS}/report.html, modules.csv, timeseries.csv")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verification Run Stats</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>
:root {
  color-scheme: light;
  --surface: #fcfcfb; --page: #f5f5f3; --text: #0b0b0b; --text-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --s1: #2a78d6; --s2: #eb6834; --border: #e1e0d9;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface: #1a1a19; --page: #121211; --text: #f0efec; --text-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --s1: #3987e5; --s2: #d95926; --border: #2c2c2a;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface: #1a1a19; --page: #121211; --text: #f0efec; --text-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --s1: #3987e5; --s2: #d95926; --border: #2c2c2a;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--text);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 0 0 2px; }
.sub, .note { color: var(--text-2); }
.note { font-size: 13px; margin: 0 0 8px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 20px 0; }
.tile, .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
.tile { padding: 12px 14px; }
.tile .v { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile .l { color: var(--text-2); font-size: 12px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 520px), 1fr)); gap: 16px; }
.card { padding: 14px 16px; min-width: 0; }
.plot { height: 280px; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: 13px; }
th, td { padding: 5px 8px; border-bottom: 1px solid var(--border); text-align: right; white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--text-2); font-weight: 500; }
td.mod { max-width: 520px; overflow: hidden; text-overflow: ellipsis; }
.scroll { overflow-x: auto; }
.full { grid-column: 1 / -1; }
</style>
</head>
<body>
<main>
  <h1>Verification run stats</h1>
  <div class="sub" id="sub"></div>
  <div class="tiles" id="tiles"></div>
  <div class="grid">
    <section class="card"><h2>Modules built per 5 minutes</h2>
      <p class="note">Throughput of the Lean build.</p><div class="plot" id="p-built"></div></section>
    <section class="card"><h2>Cores: Lean jobs running vs. busy</h2>
      <p class="note">Lean jobs = compile time overlapping each bucket ÷ 5 min. Busy cores = CloudWatch CPU × vCPUs.
        Jobs below the vCPU line: the dependency graph limits the build.
        Busy cores below the jobs: the jobs themselves are not using a full core each.</p>
      <div class="plot" id="p-cores"></div></section>
    <section class="card"><h2>Memory used (GB)</h2>
      <p class="note">From sar, every 10 minutes.</p><div class="plot" id="p-mem"></div></section>
    <section class="card"><h2>EBS data volume throughput (MB/s)</h2>
      <p class="note">CloudWatch, summed per 5 minutes.</p><div class="plot" id="p-ebs"></div></section>
    <section class="card"><h2>Compile time per module</h2>
      <p class="note" id="hist-note"></p><div class="plot" id="p-hist"></div></section>
    <section class="card"><h2>Compile time vs. .olean size</h2>
      <p class="note" id="scatter-note"></p><div class="plot" id="p-scatter"></div></section>
    <section class="card"><h2>Cumulative cost (USD)</h2>
      <p class="note">Spot price × instance time, plus the data volume. An estimate.</p>
      <div class="plot" id="p-cost"></div></section>
    <section class="card"><h2>Correlations</h2>
      <p class="note">Pearson r over the build window, unless noted. Correlation is not causation.</p>
      <div class="scroll"><table id="t-corr"></table></div></section>
    <section class="card full"><h2>Libraries</h2>
      <p class="note">Cost share splits the total by compile time.</p>
      <div class="scroll"><table id="t-libs"></table></div></section>
    <section class="card full"><h2>Slowest modules</h2>
      <div class="scroll"><table id="t-slow"></table></div></section>
  </div>
</main>
<script>
const D = __DATA__;
const fmt = (v, d = 0) => v == null ? "–" : v.toLocaleString("en-US", {minimumFractionDigits: d, maximumFractionDigits: d});
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

document.getElementById("sub").textContent =
  `Snapshot ${D.fetched} UTC · status: ${D.status} · ` +
  D.instances.map(i => `${i.type} in ${i.az} (${i.from}–${i.to.slice(11)})`).join(", ");

const T = D.tiles;
const tiles = [
  [`${fmt(T.built)} / ${fmt(T.total)}`, `modules built (${fmt(100 * T.built / T.total, 1)}%)`],
  [`${fmt(T.elapsed_h, 1)} h`, "since first launch"],
  [`${fmt(T.compile_h, 0)} h`, "total compile time"],
  [`${fmt(T.jobs_mean, 1)}`, "Lean jobs at once (mean)"],
  [`${fmt(T.busy_mean, 1)} / ${fmt(T.vcpus)}`, "busy cores (mean)"],
  [`$${fmt(T.cost, 2)}`, `cost so far ($${fmt(T.cost_per_1k, 3)} per 1k modules)`],
  [T.eta_h ? `${fmt(T.eta_h, 1)} h` : "–", `to go at the last hour's pace (${fmt(T.last_hour)}/h)`],
  [`${fmt(T.warnings)} / ${fmt(T.errors)}`, "warnings / errors"],
];
document.getElementById("tiles").innerHTML =
  tiles.map(([v, l]) => `<div class="tile"><div class="v">${esc(v)}</div><div class="l">${esc(l)}</div></div>`).join("");

function table(id, head, rows) {
  document.getElementById(id).innerHTML =
    `<thead><tr>${head.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>` +
    rows.map(r => `<tr>${r.map((c, i) => `<td${i === 0 && id === "t-slow" ? ' class="mod"' : ""}>${esc(c)}</td>`).join("")}</tr>`).join("") +
    "</tbody>";
}
table("t-corr", ["Pair", "Grain", "r"], D.corr.map(c => [c.pair, c.grain, c.r == null ? "n/a" : (c.r > 0 ? "+" : "") + c.r.toFixed(2)]));
table("t-libs", ["Library", "Built", "Total", "Done", "Compile h", "Median s", "p95 s", "Max s", "Warnings/module", "Cost share"],
  D.libs.map(l => [l.library, fmt(l.built), fmt(l.total), `${fmt(100 * l.built / l.total, 1)}%`, fmt(l.compile_h, 1),
                   fmt(l.median_s, 1), fmt(l.p95_s, 1), fmt(l.max_s, 0), fmt(l.warnings_per_module, 2), `$${fmt(l.cost_share, 2)}`]));
table("t-slow", ["Module", "Library", "Compile s", ".olean KB", "Finished (UTC)"],
  D.slowest.map(m => [m.module, m.library, fmt(m.s, 1), fmt(m.olean_kb), m.finish]));
document.getElementById("hist-note").textContent =
  `${fmt(D.scatter.count)} modules, log-spaced bins.`;
document.getElementById("scatter-note").textContent =
  D.scatter.sampled ? `Random sample of ${fmt(D.scatter.size.length)} of ${fmt(D.scatter.count)} modules, log–log.` :
  `${fmt(D.scatter.count)} modules, log–log.`;

function layout(extra = {}) {
  const ax = {gridcolor: css("--grid"), linecolor: css("--axis"), zeroline: false, tickfont: {color: css("--muted"), size: 11},
              titlefont: {color: css("--text-2"), size: 12}, automargin: true};
  return Object.assign({
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", font: {color: css("--text"), family: "system-ui, sans-serif"},
    margin: {l: 48, r: 12, t: 8, b: 36}, showlegend: false, hovermode: "x unified",
    hoverlabel: {bgcolor: css("--surface"), bordercolor: css("--axis"), font: {color: css("--text")}},
    legend: {orientation: "h", y: 1.12, x: 0, font: {color: css("--text-2"), size: 12}},
  }, extra, {xaxis: Object.assign({}, ax, extra.xaxis || {}), yaxis: Object.assign({}, ax, extra.yaxis || {})});
}
const line = (y, name, color, extra = {}) => Object.assign({x: D.t, y, name, type: "scatter", mode: "lines",
  line: {color, width: 2}, connectgaps: false, hovertemplate: "%{y:,.1f}"}, extra);
const cfg = {displayModeBar: false, responsive: true};

function draw() {
  const s1 = css("--s1"), s2 = css("--s2"), muted = css("--muted");
  Plotly.react("p-built", [{x: D.t, y: D.built, type: "bar", name: "modules", marker: {color: s1}, hovertemplate: "%{y:,}"}],
    layout({bargap: 0.15, yaxis: {rangemode: "tozero"}, xaxis: {title: {text: "UTC"}}}), cfg);
  Plotly.react("p-cores", [
    line(D.jobs, "Lean jobs running", s1),
    line(D.busy, "busy cores", s2),
    line(D.vcpus, "vCPUs", muted, {line: {color: muted, width: 1}}),
  ], layout({showlegend: true, yaxis: {rangemode: "tozero"}}), cfg);
  Plotly.react("p-mem", [line(D.mem, "used", s1, {mode: "lines+markers", marker: {size: 8, color: s1}, connectgaps: true})],
    layout({yaxis: {rangemode: "tozero", range: D.mem_total ? [0, D.mem_total] : undefined}}), cfg);
  Plotly.react("p-ebs", [line(D.write, "write", s1), line(D.read, "read", s2)],
    layout({showlegend: true, yaxis: {rangemode: "tozero"}}), cfg);
  Plotly.react("p-hist", [{
      x: D.hist.lo.map((lo, i) => Math.sqrt(lo * D.hist.hi[i])), y: D.hist.n, type: "bar", marker: {color: s1},
      width: D.hist.lo.map((lo, i) => (D.hist.hi[i] - lo) * 0.9),
      customdata: D.hist.lo.map((lo, i) => [lo, D.hist.hi[i]]),
      hovertemplate: "%{customdata[0]:.1f}–%{customdata[1]:.1f} s: %{y:,} modules<extra></extra>"}],
    layout({hovermode: "closest", xaxis: {type: "log", dtick: 1, title: {text: "seconds"}}, yaxis: {rangemode: "tozero"}}), cfg);
  Plotly.react("p-scatter", [{
      x: D.scatter.size.map(b => b / 1024), y: D.scatter.dur, text: D.scatter.name, type: "scattergl", mode: "markers",
      marker: {color: s1, size: 4, opacity: 0.35},
      hovertemplate: "%{text}<br>%{x:,.0f} KB · %{y:,.1f} s<extra></extra>"}],
    layout({hovermode: "closest", xaxis: {type: "log", dtick: 1, title: {text: ".olean KB"}}, yaxis: {type: "log", dtick: 1, title: {text: "compile s"}}}), cfg);
  Plotly.react("p-cost", [line(D.cum_cost, "cost", s1, {hovertemplate: "$%{y:,.2f}"})],
    layout({yaxis: {rangemode: "tozero", tickprefix: "$"}}), cfg);
}
draw();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
new MutationObserver(draw).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
