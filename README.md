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

📝 **Blog post:** [Did Claude Prove the Riemann Hypothesis? 🤔 Let's Ask a Spot Instance](https://builder.aws.com/content/3MQgDMMW6Kg6qZSjR9ozgcsogAv/did-claude-prove-the-riemann-hypothesis-lets-ask-a-spot-instance) on AWS Builder Center ([source](blog/ask-a-spot-instance.md)): the source now includes my completed AWS build, axiom audit and measured cost estimate.

![Domain coloring of the Riemann ξ function: spinning pinwheels, one per zero, all lined up on the critical line](docs/domain-coloring.gif)

<sub>The completed zeta function ξ(s) over 0 ≤ Im(s) ≤ 60, colored by phase. Each pinwheel is a zero of ζ, and the picture is mirror-symmetric about the critical line Re(s) = ½, where every zero shown sits. Made with [`docs/domain_coloring.py`](docs/domain_coloring.py).</sub>

## About

On November 20th 2026, [Levent Alpöge announced a proof](https://alpo.ge/remark.pdf) of the Riemann Hypothesis found together with Claude, along with a Lean 4 formalization of the argument. The formalization is distributed as a 14-part split archive (`lean.tar.gz.part01` to `part14`) hosted at [alpo.ge](https://alpo.ge).

This repository hosts the formalization, in [`lean/`](lean). A script, `fetch_merge.sh`, fetches the parts from alpo.ge, merges them and unpacks the Lean project, so you can read it and build it yourself. My independent run built all 175,187 project modules and audited the five main theorems' axioms. The [verification report](https://jgalego.github.io/riemann/) documents the results and the checks I did not run.

![ζ(½ + it) winds through the origin at each nontrivial zero, and each zero lights up on the critical line Re(s) = ½](docs/zeta.gif)

<sub>The path of ζ(½ + it) for 0 ≤ t ≤ 52. Every time it passes through 0, there's a zero of ζ on the critical line. The Riemann Hypothesis says *all* the nontrivial zeros are there. Made with [`docs/zeta.py`](docs/zeta.py).</sub>

## How the proof works

This is a plain-language sketch, drawn from the top of the Lean code and from the [announcement](https://alpo.ge/remark.pdf). The first two steps can be read straight off the code. The third follows the announcement, which the author describes as a rough timeline rather than a write-up, so treat it as a map, not an explanation.

**1. Only the right half needs checking.** The zeros of [ζ](https://en.wikipedia.org/wiki/Riemann_zeta_function) are mirror-symmetric about the line Re(s) = ½: a zero to the left of the line would have a twin to the right. So "every zero is on the line" is the same as "there are no zeros to the right of it". [`RhOfRight.lean`](lean/src/Formalism/GammaFactorNonvanishing/RhOfRight.lean#L5) proves this in about 60 lines from ζ's [functional equation](https://en.wikipedia.org/wiki/Functional_equation_(L-function)), which goes back to [Riemann's 1859 paper](https://en.wikipedia.org/wiki/On_the_Number_of_Primes_Less_Than_a_Given_Magnitude) ([English translation](https://www.claymath.org/wp-content/uploads/2023/04/Wilkins-translation.pdf)) and is Mathlib's [`riemannZeta_one_sub`](https://leanprover-community.github.io/mathlib4_docs/Mathlib/NumberTheory/LSeries/RiemannZeta.html#riemannZeta_one_sub) ([used here](lean/src/Formalism/GammaFactorNonvanishing/RhOfRight.lean#L28)). [`CritLine.lean`](lean/src/Formalism/GammaFactorNonvanishing/CritLine.lean#L11-L27) does the same for [Dirichlet L-functions](https://en.wikipedia.org/wiki/Dirichlet_L-function) (Mathlib's [`DirichletCharacter.LFunction`](https://leanprover-community.github.io/mathlib4_docs/Mathlib/NumberTheory/LSeries/DirichletContinuation.html#DirichletCharacter.LFunction)), using their functional equation [`completedLFunction_one_sub`](https://leanprover-community.github.io/mathlib4_docs/Mathlib/NumberTheory/LSeries/DirichletContinuation.html#DirichletCharacter.IsPrimitive.completedLFunction_one_sub) ([used here](lean/src/Formalism/GammaFactorNonvanishing/CritLine.lean#L22)). ζ is the simplest Dirichlet L-function, the character mod 1 ([`LFunction_modOne_eq`](https://leanprover-community.github.io/mathlib4_docs/Mathlib/NumberTheory/LSeries/DirichletContinuation.html#DirichletCharacter.LFunction_modOne_eq)), so the Riemann Hypothesis drops out as a special case of the generalized one ([`rh_of_every`](lean/src/Formalism/GammaFactorNonvanishing/CritLine.lean#L29-L32)).

**2. Everything else proves one statement.** The remaining ~175,000 files prove [`EveryCharacter`](lean/src/Development/AutomorphicArithmetic/CharacterBoxes/EveryCharacter.lean#L13-L14): no Dirichlet L-function has a zero with real part greater than ½. That is the [Generalized Riemann Hypothesis](https://en.wikipedia.org/wiki/Generalized_Riemann_hypothesis), and it is where the new mathematics lives. The main theorems in [`Main.lean`](lean/src/Main.lean#L4-L12) cover ζ, every Dirichlet L-function and the primitive ones, with the statements spelled out in [`Statements.lean`](lean/src/Statements.lean#L3-L16).

**3. The idea: borrow a trick from finite fields.** The finite-field analogue of the Riemann Hypothesis is part of the [Weil conjectures](https://en.wikipedia.org/wiki/Weil_conjectures), proved by Weil for curves (see his [1949 paper](https://doi.org/10.1090/S0002-9904-1949-09219-4)) and by [Deligne](https://www.numdam.org/item/PMIHES_1974__43__273_0/) in general. Stepanov found an elementary proof for curves (see [Bombieri's Bourbaki exposé](https://www.numdam.org/item/SB_1972-1973__15__234_0/) and [Schmidt's book](https://doi.org/10.1007/BFb0080437)). He builds an auxiliary polynomial that vanishes to high order at every point being counted, and that multiplicity forces the count to be close to its expected value. Whole numbers have no notion of vanishing to high order ("something like differentiating whole numbers", as the announcement puts it).

The new ingredient is to fake multiplicity with *chains of [theta lifts](https://en.wikipedia.org/wiki/Theta_correspondence)* (see [Howe](https://doi.org/10.1090/S0894-0347-1989-0985172-6) and [Rallis](https://www.numdam.org/item/CM_1984__51_3_333_0/)). These carry [automorphic forms](https://en.wikipedia.org/wiki/Automorphic_form) from one [unitary group](https://en.wikipedia.org/wiki/Unitary_group) to a larger one, step by step. In Lean, a chain is the inductive [`IsIterLift`](lean/src/Development/AbelianVarietyTheory/BlockTheory/TwistedDiscreteSeries/IteratedLiftChains.lean#L91-L99), where each step integrates against a theta kernel. Each lift plays the role of one more order of vanishing. For a sum over primes up to X, weighted by the [von Mangoldt function](https://en.wikipedia.org/wiki/Von_Mangoldt_function), the chain is about √X lifts long.

A positivity argument built on the [Guinand–Weil explicit formula](https://en.wikipedia.org/wiki/Explicit_formulae_for_L-functions) ([Guinand 1948](https://doi.org/10.1112/plms/s2-50.2.107)) then turns this into "no zeros to the right of ½". The explicit formula for Dirichlet characters appears in Lean as [`norm_psi0_add_zsum_le`](lean/src/Development/AbelianVarietyTheory/Proofs/GlobalInterpolation/ExplicitFormulaClosedStatement.lean#L12).

The full argument also uses:

- [Iwasawa towers](https://en.wikipedia.org/wiki/Iwasawa_theory) ([Iwasawa 1959](https://doi.org/10.1090/S0002-9904-1959-10317-7))
- Bost's slope inequality, from [Arakelov theory](https://en.wikipedia.org/wiki/Arakelov_theory) (see [Bost's Bourbaki exposé](https://www.numdam.org/item/SB_1994-1995__37__115_0/))
- a [relative trace formula](https://en.wikipedia.org/wiki/Relative_trace_formula)
- Bessel periods, from the [Gan–Gross–Prasad conjecture](https://en.wikipedia.org/wiki/Gan%E2%80%93Gross%E2%80%93Prasad_conjecture) ([Gan, Gross and Prasad](https://arxiv.org/abs/0909.2999)). In Lean, the Bessel period of a theta lift is computed in [`besselPeriod_thetaLift_eq_integral_kernel`](lean/src/Development/AbelianVarietyTheory/Proofs/DecoratedLatticeProofs/BesselPeriodThetaLiftEqIntegralKernelStatement.lean#L13), with seesaw identities such as [`seesaw_fubini_two_lifts_moderate_growth_kernels`](lean/src/Development/AbelianVarietyTheory/Proofs/DecoratedLatticeProofs/SeesawFubiniTwoLiftsModerateGrowthKernelsStatement.lean#L15)
- a weakened form of [Vandiver's conjecture](https://en.wikipedia.org/wiki/Kummer%E2%80%93Vandiver_conjecture)
- [Behrend sets](https://en.wikipedia.org/wiki/Salem%E2%80%93Spencer_set) ([Behrend 1946](https://doi.org/10.1073/pnas.32.12.331)), stated in Lean as [`BehrendFree` and `BehrendBound`](lean/src/Formalism/BehrendSets/Statement.lean#L48-L56)

Lean names don't map one-to-one onto these ideas, so Lean links are given only where a file clearly matches.

The build statistics reflect that structure. Of about 4,000 hours of summed module compile time, 58% went to [abelian variety](https://en.wikipedia.org/wiki/Abelian_variety) and [CM](https://en.wikipedia.org/wiki/Complex_multiplication) theory ([`Development/AbelianVarietyTheory`](lean/src/Development/AbelianVarietyTheory)), 15% to automorphic arithmetic, including the theta correspondence ([`Development/AutomorphicArithmetic`](lean/src/Development/AutomorphicArithmetic)), and 8% to the automorphic spectrum ([`Development/AutomorphicSpectrum`](lean/src/Development/AutomorphicSpectrum)). These are per-module elapsed times, not measured CPU usage. The slowest single module, at 22 minutes, is [a theta-correspondence file](lean/src/Development/AutomorphicArithmetic/ThetaCorrespondence/ThetaPairLifts/ThirdPlaceSeries/SiegelEisensteinSecondTermHypotheses.lean) about [Siegel–Eisenstein series](https://en.wikipedia.org/wiki/Siegel_Eisenstein_series) (see also the [Siegel–Weil formula](https://en.wikipedia.org/wiki/Siegel%E2%80%93Weil_formula) and [Weil 1965](https://doi.org/10.1007/BF02391774)).

The verification run built all 175,187 modules with no errors. `#print axioms` (run by [`verify.sh`](verify.sh)) shows the five main theorems depend only on [Lean's standard axioms](https://lean-lang.org/theorem_proving_in_lean4/Axioms-and-Computation/) (`propext`, `Classical.choice`, `Quot.sound`). So there is no `sorry` and no extra assumption. The statement being proved is Mathlib's own [`RiemannHypothesis`](https://leanprover-community.github.io/mathlib4_docs/Mathlib/NumberTheory/LSeries/RiemannZeta.html#RiemannHypothesis), not a custom definition. Lean's check says the argument is logically valid. It doesn't make the argument readable: the author says it is "incredibly long" and that a proper write-up is still to come.

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

### Build in the cloud

The project has about 175,000 modules, too many for most laptops. [`verify.sh`](verify.sh) builds it from scratch on any Linux machine and checks that the main theorems use only the standard axioms (`propext`, `Classical.choice`, `Quot.sound`). The scripts in [`aws/`](aws) run it on a 192-core EC2 spot instance, and keep progress on a separate volume so the build resumes after a spot interruption:

```bash
aws/launch.sh          # launch (or resume) the instance
aws/watch.sh           # relaunch after interruptions, fetch logs and clean up when done
python3 aws/tui.py     # live dashboard
```

To correlate build progress with resource usage and cost, `aws/stats.sh` collects per-module finish and compile times, the full build log, sysstat history, CloudWatch metrics for the instance and data volume, and spot prices into `aws/results/stats/`. `aws/watch.sh` runs it every 30 minutes and before tearing anything down. `aws/analyze.py` (standard library only) joins it all into `modules.csv`, `timeseries.csv` and an HTML report:

```bash
aws/stats.sh           # snapshot statistics from the running build
python3 aws/analyze.py # write aws/results/stats/report.html
```

![Dashboard tracking the build on a c6a.48xlarge](docs/tui.gif)

The [completed verification run](https://github.com/JGalego/riemann/releases/tag/verification-2026-11-22) includes the build log, axiom audit, per-module data and checksums. See the [verification report](https://jgalego.github.io/riemann/) for provenance and limitations.

## References

### Primary sources

- Levent Alpöge, [*Remark*](https://alpo.ge/remark.pdf) (2026-11-20). The four-page note announcing the proof, with links to the Lean archive.
- Lean and Mathlib communities, [*Lean verification of the Riemann Hypothesis*](https://leanprover-community.github.io/blog/posts/riemann-hypothesis/) (2026-11-21). Joint statement, endorsed by the Lean FRO and the Mathlib Initiative, that certifies both the statement and the proof ([announcement on X](https://x.com/leanprover/status/2124159121645801562)).
- Levent Alpöge and Ralph Furman, [*More than two thirds of the zeta zeros are simple and on the critical line*](https://arxiv.org/abs/2608.13637) (arXiv:2608.13637). The earlier result with Claude that raised the proportion of zeros known to be on the critical line from about 41.6% to more than two thirds. See also Youness Lamzouri, [*A new proof that more than 2/3 of the zeros…*](https://arxiv.org/abs/2609.02882) (arXiv:2609.02882).

### Verification

- Andrew Sutherland (MIT), [*RH Lean proof: verification files*](https://math.mit.edu/~drew/grh/). An independent build, axiom checks and kernel replays with several independent checkers.
- [leanprover-community/riemann-hypothesis](https://github.com/leanprover-community/riemann-hypothesis). The community's Comparator setup that checks the proved theorem against Mathlib's `RiemannHypothesis`.
- Kevin Buzzard, [*"…and the proof is checked in Lean"*](https://xenaproject.wordpress.com/2026/11/12/and-the-proof-is-checked-in-lean/) (Xena Project). The four criteria the joint statement uses to judge whether a Lean proof can be trusted.
- [CocoSgt/riemann-hypothesis-lean](https://github.com/CocoSgt/riemann-hypothesis-lean). A GitHub mirror of the Lean archive, with build instructions.

### News

- Scientific American, [*Anthropic mathematician claims to prove Riemann hypothesis, most famous problem in all of math*](https://www.scientificamerican.com/article/anthropic-mathematician-claims-to-prove-riemann-hypothesis-most-famous-problem-in-all-of-math/)
- Scientific American, [*The AI Riemann hypothesis proof is true, independent mathematicians confirm*](https://www.scientificamerican.com/article/the-ai-riemann-hypothesis-proof-is-almost-certainly-true-independent-mathematicians-attest/)
- Nature, [*Mathematicians astounded by AI and human-written proofs of iconic Riemann hypothesis*](https://www.nature.com/articles/d41586-026-03812-7)
- Science, [*AI may have solved math's greatest mystery. Can humans understand the answer?*](https://www.science.org/content/article/ai-may-have-solved-math-s-greatest-mystery-can-humans-understand-answer)
- The Washington Post, [*Using Anthropic AI, mathematician claims to have solved Riemann hypothesis*](https://www.washingtonpost.com/technology/2026/11/20/using-anthropic-ai-mathematician-claims-have-solved-riemann-hypothesis/)
- Phys.org, [*Anthropic researcher says he and Claude proved the Riemann hypothesis, first posed in 1859*](https://phys.org/news/2026-11-anthropic-claude-math-greatest-riddle.html)
- SiliconANGLE, [*Anthropic researcher claims to have proven Riemann hypothesis using Claude*](https://siliconangle.com/2026/11/20/anthropic-researcher-claims-to-have-proven-riemann-hypothesis-using-claude/)
- IBM Think, [*Anthropic mathematician claims AI helped solve a 167-year-old math mystery*](https://www.ibm.com/think/news/anthropic-claude-riemann-hypothesis-math-proof)

### Guides, blogs and discussion

- [*The proof of the Riemann hypothesis announced by L. Alpöge: a guide to its architecture*](https://nasqret.github.io/riemann-rev/guide/proof-guide.pdf). An 11-page map of the proof, with links into the Lean archive ([repo and status dashboard](https://github.com/nasqret/riemann-rev)).
- Lean 4 Dev, [*The Riemann Hypothesis: a claimed proof, delivered in Lean*](https://lean4.dev/proofs/riemann-hypothesis-claim)
- DEV Community, [*A Claude-Assisted Riemann Proof Passed Lean's Compiler. That's Not the Same as Proving It*](https://dev.to/euk_ela_a3e7ed01aa3f7314e/a-claude-assisted-riemann-proof-passed-leans-compiler-thats-not-the-same-as-proving-it-2kp4)
- Hacker News, [*Levent Alpöge claims proof of the Generalized Riemann Hypothesis*](https://news.ycombinator.com/item?id=50574968)

### Videos

- Bolting, [*Inside the Alpöge–Claude Proof of the Riemann Hypothesis: a Guided Tour for Analysis Students*](https://www.youtube.com/watch?v=aKWgsUQVLYU)
- Dirac, [*Just in: An 11-page map of the claimed Riemann Hypothesis proof*](https://www.youtube.com/watch?v=YxMoQ76HgtA)
- Dirac, [*Just in: The Riemann Hypothesis Proof Passed an Independent Check*](https://www.youtube.com/watch?v=d-ANPBNcTd8)
- ThursdAI, [*Riemann solved?! Live Lean 4 verification watch*](https://www.youtube.com/watch?v=00fS-gxuBRo)
- Samuel Allen Alexander, [*The Riemann Hypothesis has been solved!! Anthropic's Levent Alpöge announces lean verified GRH!*](https://www.youtube.com/watch?v=_FrHwi9ZVtg)

### Background

- Clay Mathematics Institute, [*Riemann Hypothesis*](https://www.claymath.org/millennium/riemann-hypothesis/). The Millennium Prize problem statement.
- 3Blue1Brown, [*But what is the Riemann hypothesis? Visualizing analytic continuation*](https://www.youtube.com/watch?v=sD0NjbwqlYw)

## License

The original contents of this repository (scripts, AWS tooling, visualizations, docs and blog post) are released under the [MIT License](LICENSE).

The Lean formalization in [`lean/`](lean) is the work of Levent Alpöge and Claude, redistributed from [alpo.ge](https://alpo.ge). The MIT License does not cover it, and it remains subject to its authors' terms. Its dependency [Mathlib](https://github.com/leanprover-community/mathlib4) is licensed under Apache 2.0.
