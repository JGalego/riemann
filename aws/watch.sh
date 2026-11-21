#!/usr/bin/env bash
# Poll the verification instance. On a spot interruption, relaunch it (progress
# is kept on the data volume). When the build finishes, fetch the logs into
# aws/results/, terminate the instance and delete the data volume.
#
# Usage: aws/watch.sh           poll every INTERVAL seconds (default 600) until done
#        aws/watch.sh --once    print status once
#        aws/watch.sh --pause   terminate the instance, keep the volume (resume: aws/launch.sh)
#        aws/watch.sh --kill    fetch whatever logs exist, terminate and delete the volume
# AUTO_RELAUNCH=0 disables relaunching after spot interruptions.
# KEEP_VOLUME=1 keeps the data volume (built .oleans, ~1.2 TB) after a successful
# build, e.g. to animate proofs later; it costs ~$0.29/h until deleted with --kill.
set -euo pipefail
cd "$(dirname "$0")"
[ -f state.env ] || { echo "No state.env: nothing launched."; exit 1; }
INTERVAL=${INTERVAL:-600}
AUTO_RELAUNCH=${AUTO_RELAUNCH:-1}
KEEP_VOLUME=${KEEP_VOLUME:-0}
TOTAL=175187
W=/home/ubuntu/riemann-work

load() {
  # shellcheck disable=SC1091
  . ./state.env
  export AWS_REGION=$REGION
  SSH=(ssh -i riemann-verify.pem -o UserKnownHostsFile=known_hosts -o StrictHostKeyChecking=accept-new
       -o ConnectTimeout=15 -o BatchMode=yes "ubuntu@$IP")
}

status() {
  "${SSH[@]}" "cat $W/STATUS 2>/dev/null || echo booting;
    n=\$(grep -cE '\] Built ' $W/build.log 2>/dev/null || echo 0); echo \"built=\$n\";
    uptime; free -g | awk '/Mem:/{print \"mem used \"\$3\"/\"\$2\" GB\"}'; df -h $W | tail -1" 2>/dev/null
}

terminate() {
  [ -n "${INSTANCE_ID:-}" ] || return 0
  aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null 2>&1 || true
  aws ec2 wait instance-terminated --instance-ids "$INSTANCE_ID" 2>/dev/null || true
  echo "Instance $INSTANCE_ID terminated."
}

delete_volume() {
  aws ec2 wait volume-available --volume-ids "$VOLUME_ID"
  aws ec2 delete-volume --volume-id "$VOLUME_ID"
  rm -f state.env known_hosts
  echo "Volume $VOLUME_ID deleted. Nothing left running or stored."
}

fetch_logs() {
  mkdir -p results
  local out
  out="results/results-$(date -u +%Y%m%dT%H%MZ).tar.gz"
  "${SSH[@]}" "cd $W && tar -czf - STATUS build.log axioms.log CheckAxioms.lean ../verify.out 2>/dev/null" \
    > "$out" && echo "Logs saved to aws/$out" || echo "Could not fetch logs."
}

load
case "${1:-}" in
  --once) status; exit ;;
  --pause) terminate; echo "Volume $VOLUME_ID kept (~\$0.29/h for 2 TB). Resume with aws/launch.sh."; exit ;;
  --kill) fetch_logs; terminate; delete_volume; exit ;;
esac

while true; do
  load
  STATE=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].[State.Name,StateReason.Code]' --output text 2>/dev/null || echo "gone -")
  HOURS=$(( ($(date +%s) - $(date -d "$FIRST_LAUNCHED" +%s)) / 3600 ))
  case "$STATE" in
    running*)
      OUT=$(status || echo unreachable)
      BUILT=$(sed -n 's/^built=//p' <<<"$OUT")
      echo "$(date -u +%T) [${HOURS}h since first launch] $(head -1 <<<"$OUT"), built ${BUILT:-?}/$TOTAL"
      case "$(head -1 <<<"$OUT")" in
        ok) fetch_logs; terminate
            if [ "$KEEP_VOLUME" = 1 ]; then
              echo "Volume $VOLUME_ID kept with the built project (~\$0.29/h). Delete with: aws/watch.sh --kill"
            else
              delete_volume
            fi
            echo "VERIFIED."; exit 0 ;;
        failed*) fetch_logs; terminate
                 echo "Build failed; volume $VOLUME_ID kept for inspection or resume (~\$0.29/h)."
                 echo "Delete it with: aws/watch.sh --kill"; exit 1 ;;
      esac ;;
    pending*) echo "$(date -u +%T) instance starting" ;;
    *Server.SpotInstanceTermination*)
      echo "$(date -u +%T) spot instance reclaimed by AWS ($STATE)"
      if [ "$AUTO_RELAUNCH" = 1 ]; then
        ./launch.sh || echo "Relaunch failed (no capacity?); retrying in ${INTERVAL}s."
      else
        echo "Resume with aws/launch.sh, or clean up with aws/watch.sh --kill."; exit 1
      fi ;;
    *)
      echo "$(date -u +%T) instance is $STATE (time budget or grace shutdown?)."
      echo "Volume $VOLUME_ID kept. Resume with aws/launch.sh, or clean up with aws/watch.sh --kill."
      exit 1 ;;
  esac
  sleep "$INTERVAL"
done
