#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["rich>=13"]
# ///
"""Live dashboard for the AWS verification run.

Reads aws/state.env (written by launch.sh) and polls the instance over one
multiplexed SSH connection. Read-only: lifecycle (relaunch, teardown) stays
with aws/watch.sh.

Usage: python3 aws/tui.py   (or: uv run aws/tui.py)      Ctrl-C to quit.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "lean" / "src"
POLL_SECONDS = 5
VOLUME_PER_HOUR = 0.29  # 2 TB gp3 at 6000 IOPS / 500 MB/s, eu-west-1
PRIZE = 1_000_000

# Imaginary parts of the first 29 nontrivial zeros of zeta (all below height 100).
ZEROS = [14.134725, 21.022040, 25.010858, 30.424876, 32.935062, 37.586178,
         40.918719, 43.327073, 48.005151, 49.773832, 52.970321, 56.446248,
         59.347044, 60.831779, 65.112544, 67.079811, 69.546402, 72.067158,
         75.704691, 77.144840, 79.337375, 82.910381, 84.735493, 87.425275,
         88.809111, 92.491899, 94.651344, 95.870634, 98.831194]

FACTS = [
    "Riemann's 1859 paper is 8 pages long. He wrote that it is 'sehr wahrscheinlich' "
    "(very likely) that all the roots are real, i.e. on the critical line.",
    "Hilbert reportedly said that if he woke after a thousand years, his first question "
    "would be: has the Riemann Hypothesis been proven?",
    "Hardy reportedly mailed a postcard claiming a proof of RH before a stormy ferry "
    "crossing, as insurance: God would not let him drown with such undeserved glory.",
    "In 1914 Hardy proved that infinitely many zeros lie on the critical line.",
    "The first nontrivial zero sits at 1/2 + 14.134725...i.",
    "Montgomery and Dyson noticed, over tea at the IAS in 1972, that zero spacings "
    "match the eigenvalue statistics of random Hermitian matrices.",
    "Platt and Trudgian (2021) checked that RH holds for all zeros up to height 3*10^12.",
    "This build checks about 17.4 million lines of Lean, roughly 8x the size of Mathlib.",
    "Every module that turns green below has been elaborated and accepted by Lean's kernel.",
]

PHASES = [("download", "Download"), ("cache", "Mathlib cache"), ("build", "Build"),
          ("axioms", "Axioms"), ("qed", "Q.E.D.")]

BUILT_RE = re.compile(r"\[\d+/\d+\] Built (\S+)(?: \(([\d.]+)(ms|s)\))?")

PROBE = r"""
W=/home/ubuntu/riemann-work
echo @status; cat $W/STATUS 2>/dev/null || echo booting
echo @phase; grep '^=== ' /home/ubuntu/verify.out 2>/dev/null | tail -1
echo @stat; grep '^cpu' /proc/stat
echo @mem; grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo
echo @load; cat /proc/loadavg
echo @disk; df -B1 --output=size,used $W 2>/dev/null | tail -1
echo @procs; pgrep -c -x lean || true
echo @oleans
if [ -d $W/lean/.lake/build/lib/lean ]; then
  cd $W/lean/.lake/build/lib/lean && find . -name '*.olean' | cut -d/ -f2 | sed 's/\.olean$//' | sort | uniq -c
fi
if [ "$FULL" = 1 ]; then
  echo @issues
  grep -c '^error' $W/build.log 2>/dev/null || echo 0
  grep -c '^warning' $W/build.log 2>/dev/null || echo 0
fi
echo @logsize; stat -c %s $W/build.log 2>/dev/null || echo 0
echo @log; tail -c +$((OFF + 1)) $W/build.log 2>/dev/null | head -c 2000000
"""


def load_state() -> dict:
    state = {}
    try:
        for line in (HERE / "state.env").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                state[k] = v
    except OSError:
        pass
    return state


def library_totals() -> Counter:
    """Modules per top-level directory, from the local sources if present."""
    totals = Counter()
    if SRC.is_dir():
        for root, _, files in os.walk(SRC):
            rel = Path(root).relative_to(SRC).parts
            for f in files:
                if f.endswith(".lean"):
                    totals[rel[0] if rel else f[:-5]] += 1
    if not totals:  # fallback: counts from the published archive
        totals.update({"Development": 141928, "Formalism": 18254, "GroundTheory": 8507,
                       "ReciprocityTheory": 3845, "CompanionTheory": 2027,
                       "PositivityTheory": 449, "Lemmata": 167, "BuildAid": 5,
                       "Supplements": 3, "Main": 1, "Statements": 1})
    return totals


class Monitor:
    """Polls the instance in a background thread; the UI reads its fields."""

    def __init__(self):
        self.lock = threading.Lock()
        self.totals = library_totals()
        self.total = sum(self.totals.values())
        self.state = load_state()
        self.reachable = False
        self.last_ok = 0.0
        self.status = "booting"
        self.phase = ""
        self.cpu: list[float] = []
        self.prev_cpu: dict[str, tuple[int, int]] = {}
        self.mem = (0, 0, 0, 0)  # total, available, swap total, swap free (kB)
        self.load = ""
        self.disk = (0, 0)
        self.procs = 0
        self.built = Counter()
        self.errors = self.warnings = 0
        self.log_off = None
        self.recent: deque = deque(maxlen=8)
        self.slowest: list[tuple[float, str]] = []
        self.history: deque = deque(maxlen=720)  # (time, built) samples
        self.tick = 0

    # -- polling -------------------------------------------------------------
    def ssh(self, ip: str, env: str) -> str | None:
        cmd = ["ssh", "-i", str(HERE / "riemann-verify.pem"),
               "-o", f"UserKnownHostsFile={HERE / 'known_hosts'}",
               "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
               "-o", "ConnectTimeout=8", "-o", "ControlMaster=auto",
               "-o", f"ControlPath={HERE}/.cm-%C", "-o", "ControlPersist=120",
               f"ubuntu@{ip}", f"{env} bash -s"]
        try:
            r = subprocess.run(cmd, input=PROBE, capture_output=True, text=True,
                               timeout=40, errors="replace")
        except subprocess.TimeoutExpired:
            return None
        return r.stdout if r.returncode == 0 else None

    def poll(self):
        self.state = load_state()
        ip = self.state.get("IP")
        if not ip:
            self.reachable = False
            return
        off = self.log_off if self.log_off is not None else -1
        full = 1 if self.tick % 6 == 0 else 0
        out = self.ssh(ip, f"OFF={max(off, 0)} FULL={full}")
        self.tick += 1
        if out is None:
            self.reachable = False
            return
        sections, cur = {}, None
        log_text = ""
        head, sep, log_text = out.partition("\n@log\n")
        for line in head.splitlines():
            if line.startswith("@"):
                cur = line[1:]
                sections[cur] = []
            elif cur:
                sections[cur].append(line)
        with self.lock:
            self.reachable, self.last_ok = True, time.time()
            self.status = (sections.get("status") or ["booting"])[0]
            self.phase = (sections.get("phase") or [""])[0]
            self._cpu(sections.get("stat", []))
            self._mem(sections.get("mem", []))
            self.load = " ".join((sections.get("load") or [""])[0].split()[:3])
            d = (sections.get("disk") or ["0 0"])[0].split()
            self.disk = (int(d[0]), int(d[1])) if len(d) == 2 else (0, 0)
            self.procs = int((sections.get("procs") or ["0"])[0] or 0)
            built = Counter()
            for line in sections.get("oleans", []):
                parts = line.split()
                if len(parts) == 2:
                    built[parts[1]] = int(parts[0])
            self.built = built
            self.history.append((time.time(), sum(built.values())))
            if "issues" in sections and len(sections["issues"]) >= 2:
                self.errors = int(sections["issues"][0] or 0)
                self.warnings = int(sections["issues"][1] or 0)
            size = int((sections.get("logsize") or ["0"])[0] or 0)
            if self.log_off is None or size < self.log_off:
                # First poll (or a fresh log): only look at the recent tail.
                self.log_off = max(0, size - 200_000)
            elif sep:
                last_nl = log_text.rfind("\n")
                if last_nl >= 0:
                    self._parse_log(log_text[:last_nl])
                    self.log_off += len(log_text[:last_nl + 1].encode("utf-8", "replace"))

    def _cpu(self, lines):
        usage = []
        for line in lines:
            f = line.split()
            if not f or f[0] == "cpu":
                continue
            vals = list(map(int, f[1:]))
            idle, total = vals[3] + vals[4], sum(vals)
            pi, pt = self.prev_cpu.get(f[0], (idle, total))
            dt = total - pt
            usage.append(0.0 if dt <= 0 else 1 - (idle - pi) / dt)
            self.prev_cpu[f[0]] = (idle, total)
        if usage:
            self.cpu = usage

    def _mem(self, lines):
        m = {}
        for line in lines:
            k, v = line.split(":", 1)
            m[k] = int(v.split()[0])
        self.mem = (m.get("MemTotal", 0), m.get("MemAvailable", 0),
                    m.get("SwapTotal", 0), m.get("SwapFree", 0))

    def _parse_log(self, text: str):
        for line in text.splitlines():
            m = BUILT_RE.search(line)
            if not m:
                continue
            name, num, unit = m.groups()
            secs = 0.0 if num is None else float(num) / (1000 if unit == "ms" else 1)
            self.recent.appendleft((name, secs))
            self.slowest.append((secs, name))
            self.slowest = sorted(self.slowest, reverse=True)[:5]

    def run(self):
        while True:
            try:
                self.poll()
            except Exception as e:  # keep the dashboard alive on odd output
                self.phase = f"probe error: {e}"
            time.sleep(POLL_SECONDS)


# -- rendering ---------------------------------------------------------------

def heat(u: float) -> str:
    stops = [(0.0, (40, 50, 90)), (0.35, (40, 140, 200)), (0.65, (60, 200, 120)),
             (0.85, (240, 200, 60)), (1.0, (240, 80, 60))]
    u = max(0.0, min(1.0, u))
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if u <= b:
            t = (u - a) / (b - a) if b > a else 0
            r, g, bl = (round(x + (y - x) * t) for x, y in zip(ca, cb))
            return f"rgb({r},{g},{bl})"
    return "rgb(240,80,60)"


def fmt_dur(secs: float) -> str:
    secs = int(max(secs, 0))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s" if m else f"{s}s"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def current_phase(mon: Monitor) -> str:
    p, s = mon.phase, mon.status
    if s == "ok":
        return "qed"
    if s.startswith("failed"):
        return "failed"
    for key, needle in (("axioms", "Checking axioms"), ("build", "Building"),
                        ("cache", "Fetching Mathlib cache"), ("download", "Downloading")):
        if needle in p:
            return key
    return "download"


def header(mon: Monitor, now: float) -> Panel:
    st = mon.state
    phase = current_phase(mon)
    keys = [k for k, _ in PHASES]
    idx = keys.index(phase) if phase in keys else -1
    pipe = Text()
    for i, (k, label) in enumerate(PHASES):
        if phase == "failed":
            style = "bold red" if i == 2 else "dim"
        elif i < idx:
            style = "green"
        elif i == idx:
            style = "bold black on bright_yellow" if k != "qed" else "bold black on green"
        else:
            style = "dim"
        mark = "✔ " if i < idx else ("▶ " if i == idx else "  ")
        pipe.append(f" {mark}{label} ", style=style)
        if i < len(PHASES) - 1:
            pipe.append(" ─ ", style="dim")
    first = st.get("FIRST_LAUNCHED")
    elapsed = now - datetime.fromisoformat(first.replace("Z", "+00:00")).timestamp() if first else 0
    price = float(st.get("PRICE") or 0)
    cost = elapsed / 3600 * (price + VOLUME_PER_HOUR)
    title = Text("ζ(s) = 0  ⟹  Re(s) = ½ ", style="bold bright_magenta")
    title.append("  verifying Alpöge & Claude's Lean proof", style="italic")
    info = Text(f"{st.get('INSTANCE_TYPE', '?')} · {st.get('AZ', '?')} · "
                f"${price:.2f}/h · up {fmt_dur(elapsed)} · ≈${cost:.2f} spent "
                f"({cost / PRIZE * 100:.5f}% of the Millennium Prize)", style="cyan")
    if not mon.reachable:
        info.append("   ⚠ instance unreachable (booting or relaunching?)", style="bold yellow")
    if phase == "failed":
        info.append(f"   ✖ {mon.status}", style="bold red")
    return Panel(Group(Align.center(title), Align.center(pipe), Align.center(info)),
                 border_style="magenta")


def critical_line(mon: Monitor, width: int) -> Panel:
    built = sum(mon.built.values())
    frac = built / mon.total if mon.total else 0
    if current_phase(mon) == "qed":
        frac = 1.0
    w = max(20, width - 6)
    height = frac * 100
    cols = {min(w - 1, int(z / 100 * w)) for z in ZEROS}
    line = Text()
    for c in range(w):
        t = (c + 0.5) / w * 100
        filled = t <= height
        if c in cols:
            line.append("●", style="bold gold1" if filled else "grey35")
        else:
            line.append("━" if filled else "─", style="bright_cyan" if filled else "grey30")
    confirmed = sum(1 for z in ZEROS if z <= height)
    axis = Text(f"Im(s)=0{' ' * max(0, w - 18)}Im(s)=100", style="dim")

    rate, eta = rates(mon)
    spark = sparkline(mon, 40)
    stats = Table.grid(padding=(0, 2))
    stats.add_row(
        Text(f"{built:,} / {mon.total:,} modules", style="bold"),
        Text(f"{frac * 100:.2f}%", style="bold bright_cyan"),
        Text(f"zeros lit: {confirmed}/{len(ZEROS)}", style="gold1"),
        Text(f"{rate:,.0f} modules/min" if rate else "measuring rate…", style="green"),
        Text(f"ETA {fmt_dur(eta)}" if eta else "ETA —", style="yellow"),
    )
    body = Group(line, axis, Text(), stats,
                 Text.assemble(("throughput ", "dim"), (spark, "green")))
    return Panel(body, title="[b]the critical line[/b]  Re(s) = ½", border_style="bright_cyan")


def rates(mon: Monitor):
    h = list(mon.history)
    if len(h) < 2:
        return 0.0, 0.0
    now_t, now_b = h[-1]
    window = [p for p in h if now_t - p[0] <= 900] or h
    t0, b0 = window[0]
    if now_t - t0 < 60 or now_b <= b0:
        return 0.0, 0.0
    rate = (now_b - b0) / ((now_t - t0) / 60)
    return rate, (mon.total - now_b) / rate * 60


def sparkline(mon: Monitor, n: int) -> str:
    h = list(mon.history)
    if len(h) < 3:
        return "…"
    deltas = [max(0, b2 - b1) / max(1e-9, t2 - t1) for (t1, b1), (t2, b2) in zip(h, h[1:])]
    deltas = deltas[-n:]
    top = max(deltas) or 1
    blocks = "▁▂▃▄▅▆▇█"
    return "".join(blocks[min(7, int(d / top * 7.999))] for d in deltas)


def libraries(mon: Monitor) -> Panel:
    t = Table.grid(padding=(0, 1))
    t.add_column(width=18)
    t.add_column(ratio=1)
    t.add_column(justify="right", width=17)
    for lib, total in mon.totals.most_common():
        done = min(mon.built.get(lib, 0), total)
        style = "green" if done >= total else "bright_cyan"
        label = Text(("✔ " if done >= total else "  ") + lib, style="green" if done >= total else "")
        t.add_row(label, ProgressBar(total=total, completed=done, complete_style=style,
                                     finished_style="green"),
                  Text(f"{done:,}/{total:,}", style="dim"))
    return Panel(t, title="[b]libraries[/b]", border_style="blue")


def modules(mon: Monitor) -> Panel:
    t = Table.grid(padding=(0, 1), expand=True)
    t.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    t.add_column(justify="right", width=6, no_wrap=True)
    t.add_row(Text("recently kernel-checked", style="bold green"), Text(""))
    for name, secs in list(mon.recent)[:6]:
        t.add_row(Text(f"✔ {name}", style="green"), Text(fmt_dur(secs), style="dim"))
    t.add_row(Text(""), Text(""))
    t.add_row(Text("boss fights (slowest modules)", style="bold red"), Text(""))
    for secs, name in mon.slowest[:4]:
        t.add_row(Text(f"⚔ {name}", style="red"), Text(fmt_dur(secs), style="bold red"))
    if mon.errors or mon.warnings:
        t.add_row(Text(f"errors {mon.errors:,} · warnings {mon.warnings:,}",
                       style="bold red" if mon.errors else "yellow"), Text(""))
    return Panel(t, title="[b]modules[/b]", border_style="green")


def machine(mon: Monitor) -> Panel:
    cpu = mon.cpu
    n = len(cpu)
    cols = 24 if n >= 96 else 16
    grid = Text()
    for i, u in enumerate(cpu):
        grid.append("■ ", style=heat(u))
        if (i + 1) % cols == 0 and i + 1 < n:
            grid.append("\n")
    avg = sum(cpu) / n if n else 0
    total, avail, stot, sfree = mon.mem
    used = total - avail
    dsize, dused = mon.disk
    t = Table.grid(padding=(0, 1))
    t.add_column(width=6)
    t.add_column(ratio=1)
    t.add_column(justify="right", width=16)
    t.add_row("CPU", ProgressBar(total=1, completed=avg, complete_style=heat(avg)),
              f"{avg * 100:.0f}% of {n}")
    t.add_row("RAM", ProgressBar(total=max(total, 1), completed=used,
                                 complete_style="red" if total and used / total > .9 else "magenta"),
              f"{human(used * 1024)}/{human(total * 1024)}")
    if stot:
        t.add_row("swap", ProgressBar(total=stot, completed=stot - sfree, complete_style="yellow"),
                  f"{human((stot - sfree) * 1024)}")
    t.add_row("disk", ProgressBar(total=max(dsize, 1), completed=dused, complete_style="blue"),
              f"{human(dused)}/{human(dsize)}")
    info = Text(f"load {mon.load} · {mon.procs} lean processes", style="dim")
    body = Group(Align.center(grid) if cpu else Text("waiting for CPU samples…", style="dim"),
                 Text(), t, info)
    return Panel(body, title=f"[b]{n or '?'} cores[/b]", border_style="yellow")


def achievements(mon: Monitor) -> list[tuple[str, str, bool]]:
    built = sum(mon.built.values())
    frac = built / mon.total if mon.total else 0
    slow = mon.slowest[0][0] if mon.slowest else 0
    done_libs = [lib for lib, tot in mon.totals.items() if mon.built.get(lib, 0) >= tot]
    ok = mon.status == "ok"
    return [
        ("Hello, ζ", "first module checked", built >= 1),
        ("1859", "1,859 modules, one per year of Riemann's paper", built >= 1859),
        ("Mathlib-sized", "8,000 modules, about one Mathlib", built >= 8000),
        ("Ten to the four", "10,000 modules", built >= 10_000),
        ("Boss fight", "a module took over 10 minutes", slow >= 600),
        ("Library cleared", f"3 libraries complete (now {len(done_libs)})", len(done_libs) >= 3),
        ("Re(s) = ½", "halfway there", frac >= 0.5),
        ("Zero-free region", "100,000 modules and no errors", built >= 100_000 and mon.errors == 0),
        ("Axioms", "only propext, choice and Quot.sound", ok),
        ("Q.E.D.", "the kernel accepts riemannHypothesis", ok),
    ]


def trophies(mon: Monitor, now: float) -> Panel:
    t = Table.grid(padding=(0, 1))
    for name, desc, got in achievements(mon):
        t.add_row(Text("🏆" if got else "· ", style="gold1" if got else "dim"),
                  Text(name, style="bold gold1" if got else "dim"),
                  Text(desc, style="" if got else "dim"))
    fact = FACTS[int(now // 20) % len(FACTS)]
    return Panel(Group(t, Text(), Text("✦ " + fact, style="italic bright_white")),
                 title="[b]achievements[/b]", border_style="gold1")


def footer() -> Text:
    last = ""
    try:
        lines = (HERE / "watch.log").read_text().strip().splitlines()
        last = lines[-1] if lines else ""
    except OSError:
        pass
    return Text.assemble(("watcher: ", "dim"), (last or "not running", ""),
                         ("   ·   Ctrl-C to quit", "dim"))


def render(mon: Monitor, console: Console) -> Layout:
    now = time.time()
    with mon.lock:
        root = Layout()
        root.split_column(Layout(header(mon, now), size=5), Layout(name="body"),
                          Layout(footer(), size=1))
        root["body"].split_row(Layout(name="left", ratio=3), Layout(name="right", ratio=2))
        left_w = console.width * 3 // 5
        root["body"]["left"].split_column(Layout(critical_line(mon, left_w), size=8),
                                          Layout(libraries(mon), size=len(mon.totals) + 2),
                                          Layout(modules(mon)))
        root["body"]["right"].split_column(Layout(machine(mon), ratio=1),
                                           Layout(trophies(mon, now), ratio=1))
    return root


def main():
    if not (HERE / "state.env").exists():
        raise SystemExit("No aws/state.env: launch the instance first (aws/launch.sh).")
    console = Console()
    mon = Monitor()
    threading.Thread(target=mon.run, daemon=True).start()
    try:
        with Live(render(mon, console), console=console, screen=True,
                  refresh_per_second=2) as live:
            while True:
                time.sleep(0.5)
                live.update(render(mon, console))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
