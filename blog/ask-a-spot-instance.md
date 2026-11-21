# Did Claude Prove the Riemann Hypothesis? 🤔 Let's Ask a Spot Instance

> A claimed Lean 4 proof of the Riemann Hypothesis has ~175000 modules. Here is how we're getting EC2 Spot to check it for less than $5 an hour, and what we learned along the way.

Levent Alpöge recently announced a proof of the Riemann Hypothesis, found together with Claude, and released a Lean 4 formalization alongside it ([remark.pdf](https://alpo.ge/remark.pdf)). Big claims need independent checks. With Lean, anyone can run that check: build the project, and if Lean's kernel accepts every proof, the theorem follows from the axioms it uses.

The catch is the size. The project has **175,187 modules** (about 17.4 million lines of Lean): roughly 21 times as many files and 7.6 times as many lines as Mathlib itself. That won't build on a laptop. So we moved it to AWS.

## ✅ What's done

- **A reproducible build script.** `verify.sh` installs Lean, downloads the 14-part archive, checks its SHA-256, pulls the Mathlib cache, runs `lake build` (with retries), and then runs `#print axioms` on the main theorems. It fails if they depend on anything beyond Lean's three standard axioms (`propext`, `Classical.choice`, `Quot.sound`). A clean build alone isn't enough: a stray `sorry` or a custom axiom would also compile.
- **Spot-friendly orchestration.** `launch.sh` ranks five 192-vCPU instance types (c7a, c6a, c7i, m6a, m7a) across every Availability Zone by spot price per vCPU, and launches the cheapest one that has capacity and costs under $4/h.
- **Progress that survives interruptions.** The sources, Mathlib and build outputs live on a separate 2 TB gp3 EBS volume. When the instance gets a two-minute interruption notice, it stops Lake and flushes the disk. `watch.sh` notices the instance is gone, launches a new one in the same AZ, reattaches the volume, and `lake build` picks up from the last finished module.
- **Guardrails against surprise bills.** There is a hard 36-hour budget counted from the first launch, and the instance shuts itself down after it finishes. Once the build passes, `watch.sh` fetches the logs and deletes everything.

## ⏳ In progress

The build is running right now on a `c6a.48xlarge` (192 vCPUs, 384 GiB) Spot instance in eu-west-1 at about $3.22/h. A few minutes in, it had built 553 of 175,187 modules, with a load average over 180. A small terminal dashboard lets us watch it.

![The dashboard tracking the build: progress along the critical line, per-library progress, a heatmap of all 192 cores, and achievements unlocked along the way](../docs/tui.gif)

## 💡 Lessons learned

1. **Calibrate before you size.** A short trial run produced about 7 MB of build output per module. Across 175k modules that's about 1.2 TB, so we gave the build its own 2 TB volume.
2. **Many cores hit limits you don't normally see.** With hundreds of Lean processes running in parallel, the default open-file limit ran out. We now raise `ulimit -n` to 1M, in the script and on the systemd unit.
3. **Don't pin one instance type.** Spot capacity for 192-vCPU machines comes and goes by AZ. Falling back through a ranked list of types and AZs beats waiting for one specific type to free up.
4. **Create the volume after the instance.** EBS volumes are tied to one AZ. Creating the volume once we know where the instance landed avoids AZ mismatches. Finding the disk by size, rather than by NVMe device name, made mounting reliable.
5. **Check your Spot vCPU quota first.** The default quota can be lower than one large instance needs, so the script checks it before launching.
6. **"It compiles" isn't "it's proven".** The kernel only vouches for what's in the code. The axiom check and a human reading of the main theorem statements are what make a passing build meaningful.

## 📡 Stay tuned!

The build is still running, so we can't say yet whether Lean accepts the proof. When it finishes, we'll share the result, the axiom report and the final AWS bill. Stay tuned!
