# Did Claude Prove the Riemann Hypothesis? 🤔 Let's Ask a Spot Instance

> A claimed Lean 4 proof of the Riemann Hypothesis has 175,187 modules. Here's how I'm checking it on EC2 Spot for less than $5 an hour, and what I've learned so far.

> ⚠️ **Work in progress.** This is a live experiment, not a finished result. The build is still running, the numbers below will change, and the scripts are evolving as we go. Nothing here should be read as a verdict on the proof (yet). Want to follow along or run it yourself? The source code is on GitHub: [github.com/JGalego/riemann](https://github.com/JGalego/riemann).


The first time I read about the Riemann Hypothesis (RH) was in Thomas Pynchon's novel *Against the Day*, which I highly recommend. In the novel, RH symbolizes the hidden structure connecting reality and possibility, suggesting that beneath the apparent chaos of existence lies an underlying order. In fact, the connection between RH and chaos is not just metaphorical, since the [statistical behaviour of Riemann zeros is mathematically linked to quantum chaos and random matrix theory](https://michaelberryphysics.wordpress.com/wp-content/uploads/2013/07/berry401.pdf), although the deeper physical relationship and its significance remains within the realm of conjecture.

The fascination with the story led me to borrow another book, the first one I'd take from the college library: [Titchmarsh's *The Theory of the Riemann Zeta-Function*](https://sites.math.rutgers.edu/~zeilberg/EM18/TitchmarshZeta.pdf), which I devoured in a couple of weeks. Or perhaps it was Mike & Ike's *Quantum Computation and Quantum Information*. For the purposes of this story, I'm going with Titchmarsh.

RH has been lurking in the background ever since. So when [Levent Alpöge announced a proof](https://alpo.ge/remark.pdf), found together with Claude and accompanied by a Lean 4 formalization, I couldn't resist trying to check it independently.

The idea is simple: let Lean's kernel check the formal proof. The catch is the size: **175,187 Lean modules**, roughly 21 times as many as Mathlib. My current setup can't handle that, so I've taken the build to AWS.

## ✅ What I've built

I use [`verify.sh`](../verify.sh) to install Lean, download the 14-part archive, fetch the Mathlib cache, and run `lake build` with retries. For the AWS run, I pin the archive's expected SHA-256. After the build, the script runs `#print axioms` on five main theorems and fails if they depend on anything beyond `propext`, `Classical.choice`, and `Quot.sound`.

- **Spot capacity.** I use [`launch.sh`](../aws/launch.sh) to rank five 192-vCPU instance types (c7a, c6a, c7i, m6a, m7a) across Availability Zones by quoted Spot price per vCPU. It filters out quotes above $4/h and tries candidates in price order until one has capacity.
- **Interruptions.** I keep the sources, Mathlib, and build outputs on a separate 2 TB gp3 EBS volume. On a two-minute Spot interruption notice, the instance stops the build and flushes the disk. [`watch.sh`](../aws/watch.sh) launches a replacement in the same AZ and reattaches the volume; Lake reuses the finished modules.
- **Time budget and cleanup.** I've set a 36-hour budget measured from the first launch, with instance shutdown timers as a backstop. On success, the watcher fetches the logs, terminates the instance, and deletes the data volume by default. On failure, it keeps the volume for inspection. That volume costs about $0.29/h even without a running instance.

## ⏳ In progress

My build is still running on a `c6a.48xlarge` Spot instance in `eu-west-1`: 192 vCPUs, 384 GiB of RAM, and about **$3.22/h for compute**. In an early snapshot, a few minutes into the run, 553 of the 175,187 modules had finished and the load average was over 180. I use a terminal dashboard to watch progress.

![My dashboard showing module progress and activity across 192 cores](../docs/tui.gif)

## 💡 Lessons learned

1. **Size the disk from a trial run.** A short run produced about 7 MB of build output per module. Across 175,187 modules, that's roughly 1.2 TB, so I chose a 2 TB volume.
2. **Raise the open-file limit.** Hundreds of parallel Lean processes exhausted the default per-process file-descriptor limit. In [`verify.sh`](../verify.sh), I use:

   ```bash
   ulimit -n 1048576
   ```

   If that fails, the script falls back to the current hard limit with `ulimit -n "$(ulimit -Hn)"`. The build runs as a transient systemd service, so I also set its limit explicitly:

   ```bash
   systemd-run --unit=riemann-verify -p LimitNOFILE=1048576 /opt/run.sh
   ```

3. **Don't pin one instance type.** Spot capacity for 192-vCPU machines varies by type and AZ. I try a ranked list of alternatives rather than waiting for one specific type to become available.
4. **Create the volume after the instance.** EBS volumes are tied to one AZ. I wait until I know where the instance landed before creating the volume there. I also locate the disk by size rather than relying on an NVMe device name.
5. **Check the Spot vCPU quota first.** The default quota can be lower than one large instance needs. My launch script checks it before trying to launch.
6. **"It compiles" isn't "it's proven".** A `sorry` or custom axiom can compile too. I also need to inspect the axioms and make sure the formalized statement is actually the Riemann Hypothesis I intend to check. My script handles the axiom audit, but it doesn't compare the theorem statement with Mathlib's accepted definition.

## 📡 What's next

Other people have already reported clean builds. I'm not claiming RH has been proved on the strength of my run. A successful kernel check would be a milestone in my independent check, not a reason to skip mathematical scrutiny.

**Update (21 November 2026):** The Lean and Mathlib communities have published a [joint statement](https://leanprover-community.github.io/blog/posts/riemann-hypothesis/), endorsed by the Lean FRO and the Mathlib Initiative, saying they have "full confidence that Alpöge's proof of the Riemann Hypothesis is correct". They report comparing the proved theorem with Mathlib's RH statement and checking the exported proof with several independent proof checkers. That's broader evidence than my build alone can provide.

When my run finishes, I'll add the build result, axiom report, and final AWS costs here.
