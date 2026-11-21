#!/usr/bin/env bash
# Collect statistics about the verification run into aws/results/stats/, to
# correlate build progress with resource usage and cost later.
#
#   modules.tsv            finish time (epoch), .olean size and path of every built module
#   build.log.gz           full Lake log: per-module compile times, warnings, errors
#   status.txt             STATUS of verify.sh at fetch time
#   instances.tsv          every instance of this run: id, type, AZ, launch time
#   sar/<instance>/        sysstat history of that instance (CSV via sadf, plus raw sa files)
#   cloudwatch/<id>.tsv    CloudWatch metrics of each instance and of the data volume
#   spot-prices.tsv        spot price history of the instance types used
#
# The sysstat history lives on the instance's root disk and disappears with the
# instance (spot interruption or teardown), so run this regularly while the build
# is running, and before aws/watch.sh deletes anything. Re-running is safe: module
# and log snapshots are replaced, sar data of earlier instances is kept.
#
# Usage: aws/stats.sh
set -euo pipefail
cd "$(dirname "$0")"
[ -f state.env ] || { echo "No state.env: nothing launched."; exit 1; }
# shellcheck disable=SC1091
. ./state.env
export AWS_REGION=$REGION
OUT=results/stats
mkdir -p "$OUT/sar" "$OUT/cloudwatch"
NOW=$(date -u +%FT%TZ)
SSH=(ssh -i riemann-verify.pem -o UserKnownHostsFile=known_hosts -o StrictHostKeyChecking=accept-new
     -o ConnectTimeout=15 -o BatchMode=yes "ubuntu@$IP")

# --- Instances of this run ----------------------------------------------------
# Terminated instances stay listed for about an hour, so the list accumulates.
touch "$OUT/instances.tsv"
{
  cat "$OUT/instances.tsv"
  aws ec2 describe-instances --filters "Name=tag:Name,Values=riemann-verify" \
    --query 'Reservations[].Instances[].[InstanceId,InstanceType,Placement.AvailabilityZone,LaunchTime]' \
    --output text | awk -v since="$FIRST_LAUNCHED" '$4 >= substr(since, 1, 16)'
} | sort -u -k1,1 | sort -k4 > "$OUT/instances.tmp"
mv "$OUT/instances.tmp" "$OUT/instances.tsv"
echo "Instances of this run:"; sed 's/^/  /' "$OUT/instances.tsv"

# --- Snapshot from the running instance ---------------------------------------
STATE=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo gone)
if [ "$STATE" = running ]; then
  TMP=$(mktemp -d)
  trap 'rm -rf "$TMP"' EXIT
  "${SSH[@]}" "IID=$INSTANCE_ID bash -s" > "$TMP/snap.tar.gz" <<'REMOTE'
set -uo pipefail
W=/home/ubuntu/riemann-work
T=$(mktemp -d); cd "$T"
find "$W/lean/.lake/build/lib/lean" -name '*.olean' -printf '%T@\t%s\t%P\n' 2>/dev/null > modules.tsv
gzip -c "$W/build.log" > build.log.gz 2>/dev/null
cp "$W/STATUS" status.txt 2>/dev/null
export_sar() {  # <sysstat dir> <output dir>
  mkdir -p "$2"
  for f in "$1"/sa[0-9]*; do
    [ -f "$f" ] || continue
    local d; d=$(basename "$f")
    cp "$f" "$2/"
    sadf -d -U "$f" -- -u ALL  > "$2/$d-cpu.csv"
    sadf -d -U "$f" -- -r ALL  > "$2/$d-mem.csv"
    sadf -d -U "$f" -- -S      > "$2/$d-swap.csv"
    sadf -d -U "$f" -- -B      > "$2/$d-paging.csv"
    sadf -d -U "$f" -- -q ALL  > "$2/$d-load.csv" 2>/dev/null || sadf -d -U "$f" -- -q > "$2/$d-load.csv"
    sadf -d -U "$f" -- -d -p   > "$2/$d-disk.csv"
    sadf -d -U "$f" -- -b      > "$2/$d-io.csv"
    sadf -d -U "$f" -- -n DEV  > "$2/$d-net.csv"
  done
  cp "$1/machine.txt" "$2/" 2>/dev/null
}
# launch.sh keeps each instance's sysstat history on the data volume, in
# sysstat/<instance id>; instances launched before that only have /var/log/sysstat.
for d in "$W"/sysstat/i-*; do
  [ -d "$d" ] && export_sar "$d" "sar/$(basename "$d")"
done
[ -d "sar/$IID" ] || export_sar /var/log/sysstat "sar/$IID"
{
  echo "boot=$(uptime -s)"; echo "vcpus=$(nproc)"
  grep MemTotal /proc/meminfo; sadf -V | head -1
} > "sar/$IID/machine.txt"
tar -czf - .
cd / && rm -rf "$T"
REMOTE
  mkdir -p "$TMP/x" && tar -xzf "$TMP/snap.tar.gz" -C "$TMP/x"
  for f in modules.tsv build.log.gz status.txt; do
    [ -s "$TMP/x/$f" ] && mv "$TMP/x/$f" "$OUT/$f"
  done
  for d in "$TMP"/x/sar/*/; do
    d=${d%/}
    rm -rf "$OUT/sar/${d##*/}" && mv "$d" "$OUT/sar/"
  done
  echo "Snapshot: $(wc -l < "$OUT/modules.tsv") modules built, status $(cat "$OUT/status.txt" 2>/dev/null || echo ?)"
else
  echo "Instance $INSTANCE_ID is $STATE: no module, log or sar snapshot this time."
fi

# --- CloudWatch ---------------------------------------------------------------
# get_metrics <out.tsv> <namespace> <dim name> <dim value> <period> <start> <metric:stat>...
get_metrics() {
  local out=$1 ns=$2 dim=$3 val=$4 period=$5 start=$6; shift 6
  local q
  q=$(printf '%s\n' "$@" | jq -R -s --arg ns "$ns" --arg dim "$dim" --arg val "$val" --argjson p "$period" '
    split("\n") | map(select(length > 0) | split(":")) | to_entries | map({
      Id: "m\(.key)", Label: (.value[0] + (if .value[1] == "Maximum" then "Max" else "" end)),
      MetricStat: {Metric: {Namespace: $ns, MetricName: .value[0], Dimensions: [{Name: $dim, Value: $val}]},
                   Period: $p, Stat: .value[1]}})')
  aws cloudwatch get-metric-data --metric-data-queries "$q" --start-time "$start" --end-time "$NOW" \
    --scan-by TimestampAscending --output json \
    | jq -r '.MetricDataResults[] | .Label as $l | [.Timestamps, .Values] | transpose[] | [$l, .[0], .[1]] | @tsv' \
    > "$out"
}
while read -r iid _ _ launched; do
  get_metrics "$OUT/cloudwatch/$iid.tsv" AWS/EC2 InstanceId "$iid" 300 "$launched" \
    CPUUtilization:Average CPUUtilization:Maximum EBSReadBytes:Sum EBSWriteBytes:Sum \
    EBSReadOps:Sum EBSWriteOps:Sum NetworkIn:Sum NetworkOut:Sum
done < "$OUT/instances.tsv"
get_metrics "$OUT/cloudwatch/$VOLUME_ID.tsv" AWS/EBS VolumeId "$VOLUME_ID" 60 "$FIRST_LAUNCHED" \
  VolumeReadBytes:Sum VolumeWriteBytes:Sum VolumeReadOps:Sum VolumeWriteOps:Sum \
  VolumeQueueLength:Average VolumeIdleTime:Sum VolumeTotalReadTime:Sum VolumeTotalWriteTime:Sum
echo "CloudWatch: $(cat "$OUT"/cloudwatch/*.tsv | wc -l) data points"

# --- Spot prices --------------------------------------------------------------
awk '{print $2, $3}' "$OUT/instances.tsv" | sort -u | while read -r t az; do
  aws ec2 describe-spot-price-history --instance-types "$t" --availability-zone "$az" \
    --product-descriptions Linux/UNIX --start-time "$FIRST_LAUNCHED" --end-time "$NOW" \
    --query 'SpotPriceHistory[].[InstanceType,AvailabilityZone,Timestamp,SpotPrice]' --output text
done | sort -k3 > "$OUT/spot-prices.tsv"
echo "Spot prices: $(wc -l < "$OUT/spot-prices.tsv") records"

echo "$NOW" > "$OUT/fetched_at.txt"
echo "Saved to aws/$OUT/"
