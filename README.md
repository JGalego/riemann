# Riemann

[![Lean 4](https://img.shields.io/badge/Lean-v4.33.1-blue?logo=lean)](https://lean-lang.org/)
[![Mathlib](https://img.shields.io/badge/Mathlib-4-informational)](https://github.com/leanprover-community/mathlib4)
[![Remark](https://img.shields.io/badge/paper-remark.pdf-b31b1b)](https://alpo.ge/remark.pdf)
[![Peer reviewed](https://img.shields.io/badge/peer%20reviewed-not%20yet-orange)](#)
[![Certified by Lean & Mathlib](https://img.shields.io/badge/Lean%20%26%20Mathlib-certified-brightgreen?logo=lean)](https://leanprover-community.github.io/blog/posts/riemann-hypothesis/)
[![Millennium Prize](https://img.shields.io/badge/Millennium%20Prize-unclaimed-gold)](#)
[![Zeros on the line](https://img.shields.io/badge/Re(s)-%3D%201%2F2%20(allegedly)-success)](#)
[![Works on my machine](https://img.shields.io/badge/works%20on-my%20machine-lightgrey)](#)

[Riemann Hypothesis proof](https://alpo.ge/remark.pdf) formalized in Lean4 by Claude & Levent Alpöge.

## About

In [*remark.pdf*](https://alpo.ge/remark.pdf), Levent Alpöge announced a proof of the Riemann Hypothesis found together with Claude, along with a Lean 4 formalization of the argument. The formalization is distributed as a 14-part split archive (`lean.tar.gz.part01` to `part14`) hosted at [alpo.ge](https://alpo.ge).

This repository hosts the formalization, in [`lean/`](lean). A script, `fetch_merge.sh`, fetches the parts from alpo.ge, merges them and unpacks the Lean project, so you can read it and build it yourself. The claim has not been independently verified, so check it for yourself.

## Getting Started

### Prerequisites

- `curl`, `tar` and `gzip`
- [elan](https://github.com/leanprover/elan), the Lean toolchain manager. It installs the toolchain pinned by the project (`leanprover/lean4:v4.33.1`).
- Plenty of disk space and patience. The download is about 250 MB, and a full Mathlib-based build is heavy.

### Download and unpack

```bash
./fetch_merge.sh
```

This downloads the 14 parts into `download/parts/`, merges them into `download/lean.tar.gz`, and extracts the Lean project to `./lean`. Parts that are already downloaded are skipped, so you can re-run it safely.

### Build

```bash
cd lean
lake exe cache get   # fetch prebuilt Mathlib binaries
lake build
```

If the build finishes without errors, the formalization has been checked by Lean's kernel.

Stay tuned for updates and further verification of the formalization.
