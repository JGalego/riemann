#!/usr/bin/env bash
# Build and check the Lean formalization from scratch on a fresh Linux machine.
#
# Installs elan, fetches the original archive from alpo.ge, checks its hash,
# pulls the Mathlib cache, builds the whole project and prints the axioms the
# main theorems depend on. Safe to re-run: finished steps are skipped and
# `lake build` resumes where it stopped.
#
# Environment:
#   WORK             working directory (default: $HOME/riemann-work)
#   EXPECTED_SHA256  sha256 of lean.tar.gz to insist on (optional)
#   BUILD_TIMEOUT    cap on each `lake build` attempt, e.g. 30m (optional)
#   BUILD_ATTEMPTS   how many times to run `lake build` before giving up (default 3)
#
# Writes $WORK/STATUS ("running", "ok", or "failed: <reason>") and logs to
# $WORK/build.log and $WORK/axioms.log.
set -euo pipefail

WORK=${WORK:-$HOME/riemann-work}
BUILD_ATTEMPTS=${BUILD_ATTEMPTS:-3}
mkdir -p "$WORK"
STATUS_FILE="$WORK/STATUS"
log() { printf '\n=== [%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
fail() { echo "failed: $*" > "$STATUS_FILE"; log "FAILED: $*"; exit 1; }
echo running > "$STATUS_FILE"

log "Machine: $(nproc) vCPU, $(free -g | awk '/Mem:/{print $2}') GB RAM"
df -h "$WORK" | tail -1

if ! command -v lake >/dev/null && [ ! -x "$HOME/.elan/bin/lake" ]; then
  log "Installing elan"
  curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh \
    | sh -s -- -y --default-toolchain none >/dev/null
fi
export PATH="$HOME/.elan/bin:$PATH"

cd "$WORK"
if [ ! -f lean/lakefile.toml ]; then
  log "Downloading lean.tar.gz parts from alpo.ge"
  mkdir -p parts
  for i in $(seq -w 1 14); do
    [ -s "parts/lean.tar.gz.part$i" ] && continue
    curl -fsSL --retry 5 -o "parts/lean.tar.gz.part$i.tmp" "https://alpo.ge/lean.tar.gz.part$i"
    mv "parts/lean.tar.gz.part$i.tmp" "parts/lean.tar.gz.part$i"
  done
  cat parts/lean.tar.gz.part?? > lean.tar.gz
  SHA=$(sha256sum lean.tar.gz | cut -d' ' -f1)
  echo "sha256(lean.tar.gz) = $SHA"
  if [ -n "${EXPECTED_SHA256:-}" ] && [ "$SHA" != "$EXPECTED_SHA256" ]; then
    fail "hash mismatch, expected $EXPECTED_SHA256"
  fi
  tar -xzf lean.tar.gz && rm -rf lean.tar.gz parts
fi
cd lean
NMOD=$(find src -name '*.lean' | wc -l)
echo "Project modules: $NMOD"

log "Fetching Mathlib cache"
lake exe cache get || fail "lake exe cache get"

STATUS=1
for attempt in $(seq 1 "$BUILD_ATTEMPTS"); do
  log "Building, attempt $attempt/$BUILD_ATTEMPTS"
  START=$(date +%s)
  set +e
  if [ -n "${BUILD_TIMEOUT:-}" ]; then
    timeout "$BUILD_TIMEOUT" lake build 2>&1 | tee -a "$WORK/build.log"
  else
    lake build 2>&1 | tee -a "$WORK/build.log"
  fi
  STATUS=${PIPESTATUS[0]}
  set -e
  log "lake build exit status $STATUS after $(( $(date +%s) - START ))s"
  du -sh .lake/build 2>/dev/null || true
  df -h "$WORK" | tail -1
  [ "$STATUS" -eq 0 ] && break
  [ "$STATUS" -eq 124 ] && fail "stopped by BUILD_TIMEOUT"
  # A failure may be transient (e.g. a Lean process killed for memory);
  # lake only redoes the modules that did not finish.
done
if [ "$STATUS" -ne 0 ]; then
  grep -E '^error' -A8 "$WORK/build.log" | tail -80 || true
  fail "lake build exited with $STATUS after $BUILD_ATTEMPTS attempts"
fi

log "Checking axioms of the main theorems"
cat > "$WORK/CheckAxioms.lean" <<'EOF'
import Main
#print axioms riemannHypothesis
#print axioms riemannZeta_eq_zero_re
#print axioms LFunction_ne_zero_of_half_lt_re
#print axioms LFunction_eq_zero_re
#print axioms LFunction_eq_zero_re_of_isPrimitive
EOF
lake env lean "$WORK/CheckAxioms.lean" | tee "$WORK/axioms.log"
[ "$(grep -c 'depends on axioms' "$WORK/axioms.log")" -eq 5 ] || fail "unexpected #print axioms output"
EXTRA=$(sed -n 's/.*depends on axioms: \[\(.*\)\]/\1/p' "$WORK/axioms.log" | tr ',' '\n' | tr -d ' ' \
        | grep -vxE 'propext|Classical\.choice|Quot\.sound' | sort -u || true)
[ -z "$EXTRA" ] || fail "non-standard axioms: $EXTRA"

echo ok > "$STATUS_FILE"
log "OK: build succeeded and main theorems use only propext, Classical.choice, Quot.sound"
