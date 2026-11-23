#!/usr/bin/env bash
# Animate theorems of the verified build with animate-lean-proofs.
#
# Launches a memory-heavy spot instance on the kept data volume (JOB=shell, see
# launch.sh), ships the animator (a local checkout ported to the project's Lean
# and Mathlib, ANIMATOR), builds it, replays each theorem's proof and fetches the
# resulting JSON into aws/results/animations/. The instance is terminated at the
# end; the data volume is kept. Render the JSON locally with Blender:
#
#   cd $ANIMATOR && blender -b --python animate_proof.py -o out_ -a -- theorem.json
#
# Usage: aws/animate.sh [FILE:THEOREM ...]   (default: the theorems below)
#   ANIMATOR       animator checkout (default: ~/git/animate-lean-proofs)
#   KEEP_INSTANCE=1  leave the instance running afterwards (it still stops after SHELL_HOURS)
set -euo pipefail
cd "$(dirname "$0")"
ANIMATOR=${ANIMATOR:-$HOME/git/animate-lean-proofs}
export INSTANCE_TYPES=${INSTANCE_TYPES:-"r7a.8xlarge r6a.8xlarge r7i.8xlarge r6i.8xlarge r5a.8xlarge"}
export MAX_PRICE=${MAX_PRICE:-2.50}
OUT=results/animations
THEOREMS=("$@")
[ ${#THEOREMS[@]} -gt 0 ] || THEOREMS=(
  src/Formalism/GammaFactorNonvanishing/RhOfRight.lean:Formalism.VW.rh_of_right
  src/Formalism/GammaFactorNonvanishing/CritLine.lean:Formalism.VW.critLine
  src/Formalism/GammaFactorNonvanishing/CritLine.lean:Formalism.VW.gammaFactor_ne_zero
  src/Formalism/GammaFactorNonvanishing/CritLine.lean:Formalism.VW.rh_of_every
)
[ -f "$ANIMATOR/Animate.lean" ] || { echo "No animator at $ANIMATOR"; exit 1; }

# --- Instance ---------------------------------------------------------------
# shellcheck disable=SC1091
. ./state.env
export AWS_REGION=$REGION
STATE=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo gone)
[ "$STATE" = running ] || JOB=shell ./launch.sh
# shellcheck disable=SC1091
. ./state.env
SSH=(ssh -i riemann-verify.pem -o UserKnownHostsFile=known_hosts -o StrictHostKeyChecking=accept-new
     -o ConnectTimeout=10 -o BatchMode=yes -o ServerAliveInterval=30 "ubuntu@$IP")
echo "Waiting for $IP to boot and mount the volume..."
for _ in $(seq 90); do
  "${SSH[@]}" 'test -f riemann-work/lean/lakefile.toml' 2>/dev/null && break
  sleep 10
done
"${SSH[@]}" 'test -f riemann-work/lean/lakefile.toml' || { echo "Volume not mounted."; exit 1; }

# --- Animator ---------------------------------------------------------------
echo "Shipping and building the animator..."
tar -czf - -C "$ANIMATOR" --exclude=.lake --exclude=__pycache__ --exclude='*.json' --exclude='*.gif' . \
  | "${SSH[@]}" 'mkdir -p animate && tar -xzf - -C animate'
"${SSH[@]}" 'bash -s' <<'REMOTE'
set -euo pipefail
[ -x /usr/bin/time ] && [ -x /usr/bin/python3 ] && dpkg -s python3-venv >/dev/null 2>&1 \
  || { sudo apt-get update -qq && sudo apt-get install -y -qq time python3-venv >/dev/null; }
# The animator highlights code with pygmentize, which needs a Pygments with a
# Lean 4 lexer (2.18+); Ubuntu's is older.
[ -x "$HOME/venv/bin/pygmentize" ] || { python3 -m venv "$HOME/venv" && "$HOME/venv/bin/pip" install -q pygments; }
"$HOME/venv/bin/pygmentize" -L lexers | grep -q lean4
if [ ! -x "$HOME/.elan/bin/lake" ]; then
  curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    | sh -s -- -y --default-toolchain none >/dev/null
fi
export PATH="$HOME/.elan/bin:$PATH"
cd animate
lake exe cache get > cache.log 2>&1 || { tail -20 cache.log; exit 1; }
lake build Animate 2>&1 | tail -3
REMOTE

# --- Animate ----------------------------------------------------------------
mkdir -p "$OUT"
for spec in "${THEOREMS[@]}"; do
  file=${spec%%:*} thm=${spec#*:}
  echo "Animating $thm ($file)..."
  # The project's leanOptions from lakefile.toml; Lake would pass these itself.
  if "${SSH[@]}" "FILE=$file THM=$thm bash -s" > "$OUT/$thm.json" 2> "$OUT/$thm.log" <<'REMOTE'
set -euo pipefail
export PATH="$HOME/venv/bin:$HOME/.elan/bin:$PATH"
ulimit -s unlimited || true
cd riemann-work/lean
/usr/bin/time -v lake env "$HOME/animate/.lake/build/bin/Animate" "$FILE" "$THM" --root src \
  -D maxHeartbeats=4000000 -D autoImplicit=false -D relaxedAutoImplicit=false \
  -D maxSynthPendingDepth=3 -D pp.unicode.fun=true
REMOTE
  then
    echo "  ok: $(wc -c < "$OUT/$thm.json") bytes, $(grep -E 'Elapsed|Maximum resident' "$OUT/$thm.log" | sed 's/^\s*//' | paste -sd ';')"
  else
    echo "  failed, see aws/$OUT/$thm.log:"; tail -5 "$OUT/$thm.log"
  fi
done

# --- Clean up ---------------------------------------------------------------
if [ "${KEEP_INSTANCE:-0}" = 1 ]; then
  echo "Instance $INSTANCE_ID left running (stops by itself; or: aws/watch.sh --pause)."
else
  aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null
  echo "Instance $INSTANCE_ID terminating; volume $VOLUME_ID kept (delete with aws/watch.sh --kill)."
fi
echo "JSON in aws/$OUT/. Render: cd $ANIMATOR && blender -b --python animate_proof.py -o out_ -a -- <json>"
