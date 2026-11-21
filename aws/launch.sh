#!/usr/bin/env bash
# Launch (or resume) a one-time EC2 spot instance that runs ../verify.sh.
#
# Capacity: tries each (instance type, AZ) in INSTANCE_TYPES, cheapest per vCPU
# first, skipping those without spot capacity or above MAX_PRICE ($/h).
#
# Progress survives spot interruptions: the work directory (sources, Mathlib,
# .lake/build) lives on a separate EBS volume that outlives the instance. If
# aws/state.env names a volume and no instance is running, this script resumes:
# it launches a new instance in the volume's AZ, re-attaches the volume and
# `lake build` continues from the last finished module.
#
# Safety nets against runaway cost:
#   - each instance shuts itself down (and so terminates) when the MAX_HOURS
#     budget, counted from the first successful launch, runs out;
#   - once the build finishes, the instance shuts down GRACE_MIN minutes later;
#   - aws/watch.sh fetches the results, terminates the instance and deletes the volume.
#
# Usage: aws/launch.sh            (then: aws/watch.sh)
set -euo pipefail
cd "$(dirname "$0")"

INSTANCE_TYPES=${INSTANCE_TYPES:-"c7a.48xlarge c6a.48xlarge c7i.48xlarge m6a.48xlarge m7a.48xlarge"}
MAX_PRICE=${MAX_PRICE:-4.00}
# A calibration run produced ~7 MB of build output per module (175k modules).
VOLUME_GB=${VOLUME_GB:-2000}
ROOT_GB=100
MAX_HOURS=${MAX_HOURS:-36}
GRACE_MIN=${GRACE_MIN:-120}
NAME=riemann-verify
EXPECTED_SHA256=9631b4f020199e580ba86178cb56c6e9b09f690c2369a80dc4334aa93f49c7fc

# --- New run or resume? -------------------------------------------------------
REGION=${REGION:-$(aws configure get region)}
AZ='' VOLUME_ID='' FIRST_LAUNCHED='' INSTANCE_ID='' INSTANCE_TYPE='' IP='' PRICE=''
# shellcheck disable=SC1091
[ -f state.env ] && . ./state.env
export AWS_REGION=$REGION
if [ -n "$INSTANCE_ID" ]; then
  STATE=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo gone)
  case "$STATE" in
    pending|running|stopping|shutting-down)
      echo "Instance $INSTANCE_ID is $STATE; not launching another (see aws/watch.sh)."; exit 1 ;;
  esac
fi
if [ -n "$VOLUME_ID" ]; then
  echo "Resuming with volume $VOLUME_ID in $AZ (first launched $FIRST_LAUNCHED)"
  aws ec2 wait volume-available --volume-ids "$VOLUME_ID"
fi
if [ -n "$FIRST_LAUNCHED" ]; then
  REMAIN_MIN=$(( MAX_HOURS * 60 - ($(date +%s) - $(date -d "$FIRST_LAUNCHED" +%s)) / 60 ))
  [ "$REMAIN_MIN" -gt 30 ] || { echo "MAX_HOURS=$MAX_HOURS budget used up; not launching."; exit 1; }
else
  REMAIN_MIN=$(( MAX_HOURS * 60 ))
fi

save_state() {
  printf 'REGION=%s\nAZ=%s\nVOLUME_ID=%s\nFIRST_LAUNCHED=%s\nINSTANCE_ID=%s\nINSTANCE_TYPE=%s\nIP=%s\nPRICE=%s\n' \
    "$REGION" "$AZ" "$VOLUME_ID" "$FIRST_LAUNCHED" "$INSTANCE_ID" "$INSTANCE_TYPE" "$IP" "$PRICE" > state.env
}

# --- Candidates: (type, AZ, price), cheapest per vCPU first --------------------
QUOTA=$(aws service-quotas get-service-quota --service-code ec2 --quota-code L-34B43A08 \
  --query 'Quota.Value' --output text 2>/dev/null || echo 0)
QUOTA=${QUOTA%.*}
CANDIDATES=$(
  for t in $INSTANCE_TYPES; do
    v=$(aws ec2 describe-instance-types --instance-types "$t" \
      --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text 2>/dev/null) || continue
    [ "$v" -le "$QUOTA" ] || { echo "skip $t: $v vCPU > spot quota $QUOTA" >&2; continue; }
    aws ec2 describe-spot-price-history --instance-types "$t" --product-descriptions Linux/UNIX \
      --start-time "$(date -u +%FT%TZ)" --query 'SpotPriceHistory[].[AvailabilityZone,SpotPrice]' \
      --output text | awk -v t="$t" -v v="$v" '{printf "%s %s %s %d %.5f\n", t, $1, $2, v, $2/v}'
  done | awk -v az="$AZ" -v max="$MAX_PRICE" '(az=="" || $2==az) && $3<=max' | sort -k5 -n
)
[ -n "$CANDIDATES" ] || { echo "No candidate under \$$MAX_PRICE/h${AZ:+ in $AZ}. Raise MAX_PRICE or wait."; exit 1; }
echo "Candidates (type AZ \$/h vCPU \$/vCPU-h), ${REMAIN_MIN} min of the ${MAX_HOURS}h budget left:"
echo "$CANDIDATES"

# --- Network and key ----------------------------------------------------------
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
AMI=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)
if [ ! -f "$NAME.pem" ]; then
  aws ec2 delete-key-pair --key-name "$NAME" >/dev/null 2>&1 || true
  aws ec2 create-key-pair --key-name "$NAME" --query KeyMaterial --output text > "$NAME.pem"
  chmod 600 "$NAME.pem"
fi
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$NAME" Name=vpc-id,Values="$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG" = None ]; then
  SG=$(aws ec2 create-security-group --group-name "$NAME" --description "SSH for $NAME" \
    --vpc-id "$VPC" --query GroupId --output text)
fi
MYIP=$(curl -fsS https://checkip.amazonaws.com)
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 \
  --cidr "$MYIP/32" >/dev/null 2>&1 || true

# --- User data ----------------------------------------------------------------
USERDATA=$(mktemp)
trap 'rm -f "$USERDATA"' EXIT
cat > "$USERDATA" <<EOF
#!/bin/bash
set -eux
shutdown -h +$REMAIN_MIN
fallocate -l 64G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile

# The data volume is attached after boot; it is the only disk larger than the root disk.
DEV=
for _ in \$(seq 360); do
  DEV=\$(lsblk -bdnpo NAME,SIZE,TYPE | awk '\$3=="disk" && \$2>$(( (ROOT_GB + 50) * 1024 * 1024 * 1024 )) {print \$1; exit}')
  [ -n "\$DEV" ] && break; sleep 5
done
[ -n "\$DEV" ]
blkid "\$DEV" || mkfs.ext4 -m 0 -L riemann "\$DEV"
mkdir -p /home/ubuntu/riemann-work
mount -o noatime "\$DEV" /home/ubuntu/riemann-work
chown ubuntu:ubuntu /home/ubuntu/riemann-work

echo '$(base64 -w0 ../verify.sh)' | base64 -d > /opt/verify.sh
cat > /opt/run.sh <<'RUN'
#!/bin/bash
# On the 2-minute spot interruption notice: stop Lake and flush the disk.
(
  while sleep 5; do
    T=\$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')
    if curl -sf -H "X-aws-ec2-metadata-token: \$T" http://169.254.169.254/latest/meta-data/spot/instance-action; then
      echo "Spot interruption notice: stopping build" >> /home/ubuntu/verify.out
      pkill -TERM -x lake; pkill -TERM -x lean; sleep 15; sync; exit
    fi
  done
) &
sudo -u ubuntu -H env WORK=/home/ubuntu/riemann-work EXPECTED_SHA256=$EXPECTED_SHA256 \
  bash /opt/verify.sh >> /home/ubuntu/verify.out 2>&1
sync
shutdown -h +$GRACE_MIN
RUN
chmod +x /opt/verify.sh /opt/run.sh
systemd-run --unit=riemann-verify -p LimitNOFILE=1048576 /opt/run.sh
EOF

# --- Launch: first candidate with capacity ------------------------------------
INSTANCE_ID=''
while read -r t az price _ _; do
  subnet=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC" Name=availability-zone,Values="$az" \
    --query 'Subnets[0].SubnetId' --output text)
  echo "Trying $t in $az at \$$price/h..."
  if OUT=$(aws ec2 run-instances \
      --image-id "$AMI" --instance-type "$t" \
      --key-name "$NAME" --security-group-ids "$SG" --subnet-id "$subnet" \
      --associate-public-ip-address \
      --instance-market-options '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time","InstanceInterruptionBehavior":"terminate"}}' \
      --instance-initiated-shutdown-behavior terminate \
      --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":$ROOT_GB,\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]" \
      --user-data "file://$USERDATA" \
      --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
      --query 'Instances[0].InstanceId' --output text 2>&1); then
    INSTANCE_ID=$OUT INSTANCE_TYPE=$t AZ=$az PRICE=$price
    break
  fi
  case "$OUT" in
    *InsufficientInstanceCapacity*|*SpotMaxPriceTooLow*|*capacity-not-available*) echo "  no capacity" ;;
    *) echo "$OUT"; exit 1 ;;
  esac
done <<<"$CANDIDATES"
[ -n "$INSTANCE_ID" ] || { echo "No spot capacity in any candidate right now. Try again later."; exit 1; }
[ -n "$FIRST_LAUNCHED" ] || FIRST_LAUNCHED=$(date -u +%FT%TZ)
save_state
echo "Launched $INSTANCE_ID ($INSTANCE_TYPE, $AZ, \$$PRICE/h)"

# --- Data volume (kept across instances), created in the instance's AZ ---------
if [ -z "$VOLUME_ID" ]; then
  VOLUME_ID=$(aws ec2 create-volume --availability-zone "$AZ" --size "$VOLUME_GB" \
    --volume-type gp3 --iops 6000 --throughput 500 \
    --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=$NAME}]" \
    --query VolumeId --output text)
  save_state
  aws ec2 wait volume-available --volume-ids "$VOLUME_ID"
  echo "Created data volume $VOLUME_ID ($VOLUME_GB GB)"
fi
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
aws ec2 attach-volume --volume-id "$VOLUME_ID" --instance-id "$INSTANCE_ID" --device /dev/sdf >/dev/null
IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
save_state
rm -f known_hosts
echo "Running at $IP with $VOLUME_ID attached. Follow with: aws/watch.sh"
echo "(ssh: ssh -i aws/$NAME.pem ubuntu@$IP)"
