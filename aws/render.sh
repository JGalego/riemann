#!/usr/bin/env bash
# Render animate-lean-proofs JSON files to video with Blender on a spot instance.
#
# Launches a compute spot instance (no data volume needed, any AZ), installs
# Blender (Ubuntu 24.04 ships 4.0.2, the version the animator was made with),
# xvfb and ffmpeg, renders JOBS files in parallel and converts each to an H.264
# MP4. The MP4s and render logs are downloaded to aws/results/animations/render/;
# the instance is terminated when the script exits, even on failure.
#
# Usage: aws/render.sh [file.json ...]   (default: aws/results/animations/*.json)
#   ANIMATOR         animator checkout (default: ~/git/animate-lean-proofs)
#   JOBS             parallel renders (default 4)
#   FETCH_LOSSLESS=1 also download Blender's lossless .mkv masters (large)
#   RESOLUTION_X, RESOLUTION_Y, FPS   passed to the animator (default 1920x1080, 60)
#   KEEP_INSTANCE=1  don't terminate the instance (it still stops after HOURS)
#   GPU=1            render on an NVIDIA GPU instance (g4dn/g5, needs the "All G and VT
#                    Spot Instance Requests" quota): Blender's OpenGL goes to the GPU
#                    through VirtualGL's EGL back end instead of software rendering
set -euo pipefail
cd "$(dirname "$0")"
ANIMATOR=${ANIMATOR:-$HOME/git/animate-lean-proofs}
GPU=${GPU:-0}
VGL_VERSION=3.1.1
if [ "$GPU" = 1 ]; then
  INSTANCE_TYPES=${INSTANCE_TYPES:-"g4dn.xlarge g4dn.2xlarge g5.xlarge g6.xlarge"}
  AMI_PARAM=/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-24.04/latest/ami-id
  ROOT_GB=120
else
  INSTANCE_TYPES=${INSTANCE_TYPES:-"c7a.8xlarge c6a.8xlarge c7i.8xlarge c6i.8xlarge m7a.8xlarge"}
  AMI_PARAM=/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
  ROOT_GB=60
fi
MAX_PRICE=${MAX_PRICE:-1.50}
HOURS=${HOURS:-3}
JOBS=${JOBS:-4}
NAME=riemann-verify
OUT=results/animations/render
FILES=("$@")
[ ${#FILES[@]} -gt 0 ] || FILES=(results/animations/*.json)
for f in "${FILES[@]}"; do [ -f "$f" ] || { echo "No such file: $f"; exit 1; }; done
[ -f "$ANIMATOR/animate_proof.py" ] || { echo "No animator at $ANIMATOR"; exit 1; }
[ -f "$NAME.pem" ] || { echo "No $NAME.pem: run aws/launch.sh once to create the key pair."; exit 1; }
REGION=${REGION:-$(aws configure get region)}
export AWS_REGION=$REGION
mkdir -p "$OUT"

# --- Launch the cheapest candidate with capacity ------------------------------
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$NAME" Name=vpc-id,Values="$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text)
[ "$SG" != None ] || { echo "No security group $NAME: run aws/launch.sh once."; exit 1; }
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 \
  --cidr "$(curl -fsS https://checkip.amazonaws.com)/32" >/dev/null 2>&1 || true
AMI=$(aws ssm get-parameter --name "$AMI_PARAM" --query Parameter.Value --output text)
CANDIDATES=$(
  for t in $INSTANCE_TYPES; do
    aws ec2 describe-spot-price-history --instance-types "$t" --product-descriptions Linux/UNIX \
      --start-time "$(date -u +%FT%TZ)" --query 'SpotPriceHistory[].[AvailabilityZone,SpotPrice]' \
      --output text | awk -v t="$t" '{print t, $1, $2}'
  done | awk -v max="$MAX_PRICE" '$3 <= max' | sort -k3 -n
)
[ -n "$CANDIDATES" ] || { echo "No candidate under \$$MAX_PRICE/h."; exit 1; }
USERDATA="#!/bin/bash
shutdown -h +$(( HOURS * 60 ))"
INSTANCE_ID=''
while read -r t az price; do
  subnet=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC" Name=availability-zone,Values="$az" \
    --query 'Subnets[0].SubnetId' --output text)
  echo "Trying $t in $az at \$$price/h..."
  if OUT_RUN=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$t" \
      --key-name "$NAME" --security-group-ids "$SG" --subnet-id "$subnet" --associate-public-ip-address \
      --instance-market-options '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time","InstanceInterruptionBehavior":"terminate"}}' \
      --instance-initiated-shutdown-behavior terminate \
      --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":'"$ROOT_GB"',"VolumeType":"gp3","DeleteOnTermination":true}}]' \
      --user-data "$USERDATA" \
      --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME-render}]" \
      --query 'Instances[0].InstanceId' --output text 2>&1); then
    INSTANCE_ID=$OUT_RUN
    break
  fi
  case "$OUT_RUN" in
    *InsufficientInstanceCapacity*|*SpotMaxPriceTooLow*|*capacity-not-available*) echo "  no capacity" ;;
    *) echo "$OUT_RUN"; exit 1 ;;
  esac
done <<<"$CANDIDATES"
[ -n "$INSTANCE_ID" ] || { echo "No spot capacity right now."; exit 1; }
echo "INSTANCE_ID=$INSTANCE_ID" > render.env
cleanup() {
  # Whatever happened, try to save finished videos before the instance goes away.
  [ -z "${IP:-}" ] || [ "$(type -t fetch)" != function ] || fetch
  if [ "${KEEP_INSTANCE:-0}" = 1 ]; then
    echo "Instance $INSTANCE_ID left running (stops after ${HOURS}h)."
  else
    aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null && echo "Instance $INSTANCE_ID terminating."
    rm -f render.env
  fi
}
trap cleanup EXIT
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "Launched $INSTANCE_ID at $IP"
SSH=(ssh -i "$NAME.pem" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR
     -o ConnectTimeout=10 -o BatchMode=yes -o ServerAliveInterval=30 "ubuntu@$IP")
for _ in $(seq 60); do "${SSH[@]}" true 2>/dev/null && break; sleep 5; done

# --- Set up and render --------------------------------------------------------
echo "Installing Blender, xvfb and ffmpeg..."
"${SSH[@]}" 'cloud-init status --wait >/dev/null; sudo apt-get update -qq &&
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq blender xvfb ffmpeg mesa-utils >/dev/null && blender --version | head -1'
VGLRUN=''
if [ "$GPU" = 1 ]; then
  echo "Installing VirtualGL $VGL_VERSION (EGL back end)..."
  "${SSH[@]}" "set -e; nvidia-smi --query-gpu=name --format=csv,noheader
    curl -fsSL -o /tmp/vgl.deb https://github.com/VirtualGL/virtualgl/releases/download/$VGL_VERSION/virtualgl_${VGL_VERSION}_amd64.deb
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq /tmp/vgl.deb >/dev/null
    xvfb-run -a /opt/VirtualGL/bin/vglrun -d egl glxinfo -B | grep -i 'OpenGL renderer'" | tee /dev/stderr \
    | grep -i 'OpenGL renderer' | grep -viqE 'llvmpipe|softpipe|swrast' \
    || { echo "OpenGL is not on the GPU (software renderer); not rendering."; exit 1; }
  VGLRUN='/opt/VirtualGL/bin/vglrun -d egl'
fi
tar -czf - -C "$ANIMATOR" --exclude=.lake --exclude=__pycache__ --exclude='*.json' --exclude='*.gif' . \
  | "${SSH[@]}" 'mkdir -p animate/in animate/out && tar -xzf - -C animate'
tar -czf - "${FILES[@]/#/}" --transform 's|.*/||' | "${SSH[@]}" 'tar -xzf - -C animate/in'
echo "Rendering ${#FILES[@]} file(s), $JOBS at a time (detached; results are fetched as they finish)..."
# The renders run detached on the instance, so a dropped SSH connection can't
# stop them; finished files are fetched by short, retried polls.
"${SSH[@]}" "RESOLUTION_X=${RESOLUTION_X:-} RESOLUTION_Y=${RESOLUTION_Y:-} FPS=${FPS:-} JOBS=$JOBS VGLRUN='$VGLRUN' bash -s" <<'REMOTE'
set -uo pipefail
cd animate
rm -f out/ALL_DONE out/progress.log
cat > render_one.sh <<'ONE'
f=$1; n=$(basename "$f" .json); s=$(date +%s)
[ -n "$RESOLUTION_X" ] || unset RESOLUTION_X; [ -n "$RESOLUTION_Y" ] || unset RESOLUTION_Y; [ -n "$FPS" ] || unset FPS
# ffmpeg writes to a temporary name, so a fetched .mp4 is always complete.
xvfb-run -a -s "-screen 0 1920x1080x24" $VGLRUN blender -b --python animate_proof.py -o "out/${n}_" -a -- "$f" \
  > "out/$n.log" 2>&1 \
  && ffmpeg -loglevel error -y -i out/"${n}"_*.mkv -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p \
       -movflags +faststart -f mp4 "out/$n.mp4.part" \
  && mv "out/$n.mp4.part" "out/$n.mp4" \
  && echo "  $n: $(( $(date +%s) - s ))s, $(du -h "out/$n.mp4" | cut -f1)" \
  || { echo "  $n: FAILED after $(( $(date +%s) - s ))s"; tail -5 "out/$n.log"; }
ONE
export RESOLUTION_X RESOLUTION_Y FPS VGLRUN
setsid nohup bash -c 'ls in/*.json | xargs -P "$JOBS" -n 1 bash render_one.sh > out/progress.log 2>&1; touch out/ALL_DONE' \
  > /dev/null 2>&1 < /dev/null &
REMOTE

# --- Fetch as files finish ----------------------------------------------------
fetch() {  # download finished videos (once) and the latest logs; never fails
  local f list
  list=$("${SSH[@]}" "cd animate/out && ls *.mp4 *.log 2>/dev/null" 2>/dev/null) || return 0
  for f in $list; do
    case "$f" in
      *.mp4) [ -s "$OUT/$f" ] && continue
             if "${SSH[@]}" "cat animate/out/$f" > "$OUT/$f.part" 2>/dev/null; then
               mv "$OUT/$f.part" "$OUT/$f"
               echo "  fetched $f ($(du -h "$OUT/$f" | cut -f1))"
             else
               rm -f "$OUT/$f.part"
             fi ;;
      *.log) "${SSH[@]}" "cat animate/out/$f" > "$OUT/$f" 2>/dev/null || true ;;
    esac
  done
}
SEEN=0
DEADLINE=$(( $(date +%s) + HOURS * 3600 - 300 ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  sleep 60
  PROGRESS=$("${SSH[@]}" "cat animate/out/progress.log 2>/dev/null; test -f animate/out/ALL_DONE && echo @ALL_DONE; true" 2>/dev/null) \
    || { echo "  (instance unreachable, retrying)"; continue; }
  NEW=$(grep -v '^@ALL_DONE$' <<<"$PROGRESS" | tail -n +$(( SEEN + 1 )))
  [ -z "$NEW" ] || { echo "$NEW"; SEEN=$(( SEEN + $(wc -l <<<"$NEW") )); }
  fetch
  grep -q '^@ALL_DONE$' <<<"$PROGRESS" && break
done
if [ "${FETCH_LOSSLESS:-0}" = 1 ]; then
  "${SSH[@]}" "cd animate/out && tar -cf - *.mkv 2>/dev/null" | tar -xf - -C "$OUT" || true
fi
echo "Saved to aws/$OUT/:"; ls -la "$OUT"
