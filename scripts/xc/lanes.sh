#!/usr/bin/env bash
# lanes.sh <first kind>: XC_LANES worker loops (lane.sh) side by side on one fix job's runner (xc-worker.yml).
# A model round mostly waits on the model's API (2026-10-27..29: 76 % of a job's time went to model rounds, the runner's
# cores idle meanwhile), and the account runs at most 20 jobs at once, so several sessions per job multiply the fleet.
# Each lane sees /opt/s5 (checkout, build cache, game data, work list) as its own overlay of the job's frozen setup: its
# writes stay its own, and the build cache keeps its paths, so no lane rebuilds what the setup restored. Each lane has its
# own model workspace ($W-<lane>) and opencode data (a round continues its own session). The lanes share the job's claim
# token, so the watchdog and the picker still count one slot per job.
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd)
P=${XC_LANES:-1}
[ "$P" -gt 1 ] || exec "$D/lane.sh" 1 "$1"
S=${S5:-/opt/s5}; LO=$S-lower; U=$RUNNER_TEMP/lanes
sudo mv "$S" "$LO" && sudo mkdir "$S" || exit 1
# capture processes per lane (each ~0.7 GB RSS): the runner's 16 GB hold the lanes' verdicts and builds together
export XC_JOBS=${XC_JOBS:-$(( 6 / P > 1 ? 6 / P : 1 ))}
pids=()
for k in $(seq 1 "$P"); do
  mkdir -p "$U/$k/up" "$U/$k/work" "$U/$k/data/opencode" "$U/$k/state"
  cp ~/.local/share/opencode/auth.json "$U/$k/data/opencode/"
  first=""; [ "$k" = 1 ] && first=$1
  # root only for the mount: the lane itself runs as the runner user, with the job's environment
  sudo -E env "PATH=$PATH" unshare -m --propagation private -- sh -c '
    mount -t overlay overlay -o "lowerdir=$1,upperdir=$2/up,workdir=$2/work" "$3" || exit 2
    exec setpriv --reuid="$4" --regid="$5" --init-groups \
      env XDG_DATA_HOME="$2/data" XDG_STATE_HOME="$2/state" W="$W-$6" "$7/lane.sh" "$6" "$8"' \
    sh "$LO" "$U/$k" "$S" "$(id -u)" "$(id -g)" "$k" "$D" "$first" &
  pids+=($!)
  # the lanes' first builds and base measurements do not all start at once
  [ "$k" -lt "$P" ] && sleep "${XC_LANE_STAGGER:-60}"
done
rc=0
for p in "${pids[@]}"; do wait "$p" || rc=1; done
echo "lanes: $P, disk free $(df -h / | awk 'NR==2{print $4}')" | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
exit $rc
