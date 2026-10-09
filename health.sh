#!/bin/bash
# FLEET HEALTH CHECK — TIGHT. Alerts within MINUTES, not hours, because the expensive failure is
# not "the driver died", it is "workers are burning tokens and producing nothing".
#
# What the loose version missed (2026-08-19): ov027 declared itself drained, the finisher
# re-dispatched it anyway, a worker wave returned 0 FILES, the driver called it "transient",
# backed off 120s and spawned another pair of large-tier workers on the same 6 residue functions.
# Two hours and several worker-hours of tokens, zero output, and the only alert was a 120-minute
# "no commit" that fired after the damage. Every check below is sized to catch that in minutes.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
SP="$(python "$KIT/kitpaths.py" state)"
REPO="$(python "$KIT/kitpaths.py" repo)"
PROG="$SP/PROGRESS.log"
STATE="$SP/wlog/health_state.txt"
INTERVAL=${HEALTH_INTERVAL:-120}       # check every 2 min
# RECALIBRATED for pull dispatch on the large band. Both of these were set when a hard 30-minute
# session timeout existed and waves committed every few minutes. Now cost is the only limit, a
# 300-byte function legitimately runs 20-60 minutes, and integration triggers at 8 pending files --
# so the old values would fire on entirely healthy work every cycle. An alert that is usually wrong
# is one I stop reading, which is exactly how a stray run_module survived a whole session unnoticed.
ZERO_WAVE_MIN=${ZERO_WAVE_MIN:-10}     # a wave that produced 0 files
# Long enough that only a genuinely stuck session trips it, and still well inside the 2h hang guard
# so there is warning before that fires.
WORKER_MAX_MIN=${WORKER_MAX_MIN:-75}   # a single headless worker running this long
STARVE_MIN=${STARVE_MIN:-150}
REPEAT_MIN=${REPEAT_MIN:-30}
cd "$REPO" || { echo "ALERT repo missing"; exit 2; }

now() { date +%s; }
mtime() { [ -f "$1" ] && stat -c %Y "$1" 2>/dev/null || echo 0; }
mins_since() { echo $(( ( $(now) - $1 ) / 60 )); }
# COMPUTE it, never scrape it. This used to grep the last `(n/m)` out of run_all's progress log,
# which is only written while the fleet runs -- so with the fleet stopped the monitor kept printing
# a frozen 11622 for hours after commits had moved the real figure to 11654. A status line that
# cannot change reads as proof that nothing is happening, which is the opposite of monitoring.
cov_now() { python "$KIT/cov.py" 2>/dev/null || echo "(cov unavailable)"; }
commit_age_min() {
  local t; t=$(git -C "$REPO" log -1 --format=%ct 2>/dev/null)
  [ -z "$t" ] && { echo 0; return; }
  echo $(( ( $(now) - t ) / 60 ))
}
ps_count() { python "$KIT/procq.py" "$@" 2>/dev/null | tr -d '\r\n '; }
# COMMITS ARRIVE AT THE FLEET'S PACE, so a fixed stall threshold is calibrated for one fleet size
# only. At four slots 90m means something is wrong; at one slot it is a normal gap between landings
# and fires every cycle until it stops being read.
stall_limit() {
  [ -n "$STALL_MIN" ] && { echo "$STALL_MIN"; return; }
  local s; s=$(tr -dc '0-9' < "$SP/PULL_SLOTS" 2>/dev/null)
  case "$s" in ''|0) s=4 ;; esac
  [ "$s" -gt 4 ] && s=4
  echo $(( 360 / s ))
}

last_ok=0
echo "health: TIGHT mode (stall>$(stall_limit)m, zero-wave>${ZERO_WAVE_MIN}m, worker>${WORKER_MAX_MIN}m, every ${INTERVAL}s)"

while true; do
  alerts=""

  # DELIBERATE STOP. `touch $SP/FLEET_STOPPED` when the fleet is meant to be down, and the checks
  # that assume it is running go quiet. Without this the monitor spends a stopped period screaming
  # "driver down / no commits / main starved" -- all true, all intended -- and a monitor whose
  # alerts are known to be wrong is one nobody reads, which is how a stray run_module survived a
  # whole session unnoticed. The reverse check replaces it: while stopped, a driver is the fault.
  if [ -f "$SP/FLEET_STOPPED" ]; then
    n=$(ps_count --count --match 'run_all.sh|supervise.sh|run_module.sh')
    [ -z "$n" ] && n=0
    [ "$n" -gt 0 ] 2>/dev/null && alerts="${alerts}ALERT stray: fleet marked STOPPED but $n driver(s) running"$'\n'
    w=$(ps_count --count --worker '\s-p\s.*DQIX')
    [ -z "$w" ] && w=0
    [ "$w" -gt 0 ] 2>/dev/null && alerts="${alerts}ALERT stray: fleet marked STOPPED but $w headless worker(s) alive"$'\n'
    if [ -n "$alerts" ]; then printf '%s' "$alerts"; else
      # A HEARTBEAT WHILE STOPPED IS PURE COST. Every stdout line becomes a conversation message, so
      # this unconditional OK billed a turn every INTERVAL -- five in eight minutes -- to repeat a
      # coverage number that cannot change while the fleet is down. Alerts above still fire on the
      # very next cycle; only the reassurance line is rate-limited.
      _ok="$SP/wlog/.last_stopped_ok"
      _age=$(( $(date +%s) - $(date -r "$_ok" +%s 2>/dev/null || echo 0) ))
      if [ "$_age" -ge 1800 ]; then
        : > "$_ok"
        echo "OK $(date '+%H:%M') fleet intentionally stopped · cov $(cov_now)"
      fi
    fi
    sleep "$INTERVAL"; continue
  fi

  # 1. Driver alive. EITHER dispatcher counts: the batch driver (supervise+run_all) or the
  # budget-driven pull loop (pull_all). Checking only for the old pair would alert continuously
  # once dispatch moved to pull_all -- a monitor that is wrong by construction gets ignored, which
  # is how a stray run_module survived a whole session unnoticed.
  # A LONE pull_worker IS A LEGITIMATE DISPATCH. Working one function at a time on a chosen address
  # skips pull_all entirely, and alerting on that fires every cycle while the work is healthy.
  n=$(ps_count --count --match 'run_all.sh|supervise.sh|pull_all.sh|pull_worker.sh')
  [ -z "$n" ] && n=0
  [ "$n" -lt 1 ] 2>/dev/null && alerts="${alerts}ALERT driver: nothing dispatching (no pull_all, supervise+run_all, or pull_worker)"$'\n'

  # 2. ZERO-YIELD WAVE / RETRY LOOP — the token-burn signature. Catch it on the spot.
  for f in "$SP"/wlog/run_*.log; do
    [ -f "$f" ] || continue
    [ "$(mins_since $(mtime "$f"))" -gt "$ZERO_WAVE_MIN" ] && continue
    hit=$(tail -6 "$f" 2>/dev/null | grep -oE "0 files[^\"]*|back off [0-9]+s, retry|no limit string" | tail -1)
    [ -n "$hit" ] && alerts="${alerts}ALERT burn: $(basename "$f" .log) -- ${hit} (workers ran, nothing produced)"$'\n'
  done

  # 3. A headless worker running far past the point of usefulness.
  # AGE IS NOT STUCKNESS, AND NEITHER IS SOURCE MTIME. Age alone alerted every cycle while a 492B
  # function worked normally for 76 minutes. Requiring "no wip/ write in 20m" as well still fired on
  # a healthy worker, because between source writes a session spends long stretches compiling and
  # gating -- 08:25 to 08:46 with nothing written was ordinary work, not a hang. The only honest
  # liveness test is whether the process is still BURNING CPU, so compare its own consumed time
  # against the previous cycle; a hung session's total stops moving while a working one climbs.
  old=$(ps_count --count --worker '\s-p\s.*DQIX' --older "$WORKER_MAX_MIN")
  [ -z "$old" ] && old=0
  if [ "$old" -gt 0 ] 2>/dev/null; then
    _cpuf="$SP/wlog/health_worker_cpu.txt"
    _cpu=$(ps_count --cpu --worker '\s-p\s.*DQIX')
    _prev=$(cat "$_cpuf" 2>/dev/null)
    if [ -n "$_cpu" ] && [ "$_cpu" = "$_prev" ]; then
      alerts="${alerts}ALERT worker: $old headless worker(s) >${WORKER_MAX_MIN}m and burning no CPU since the last check -- hung"$'\n'
    fi
    [ -n "$_cpu" ] && echo "$_cpu" > "$_cpuf"
  else
    rm -f "$SP/wlog/health_worker_cpu.txt"
  fi

  # 4. Nothing landing.
  stalled=$(commit_age_min)
  _stall=$(stall_limit)
  [ "$stalled" -ge "$_stall" ] && alerts="${alerts}ALERT stall: no commit in ${stalled}m (limit ${_stall}m)"$'\n'

  # 4b. AN UNCRACKED IDIOM IS A MAIN-THREAD JOB, and nothing was routing it to one. blockercheck
  # holds the dispatcher when one ADDRESS keeps coming back, which is a hard function; a class where
  # eight DIFFERENT functions each die once is the opposite shape and never tripped anything, so
  # SCHED sat at 8 members with two hand-cracks that were never written up as a rule. Surfacing it
  # is the whole fix -- the fleet should keep working other functions while the idiom gets cracked.
  _crackf="$SP/wlog/.last_crack_alert"
  _cage=$(( $(now) - $(date -r "$_crackf" +%s 2>/dev/null || echo 0) ))
  if [ "$_cage" -ge "${CRACK_ALERT_EVERY:-21600}" ]; then
    _crack=$(python "$KIT/blockercheck.py" 2>/dev/null | grep '^CRACK:')
    if [ -n "$_crack" ]; then
      : > "$_crackf"
      alerts="${alerts}$(printf '%s\n' "$_crack" | sed 's/^CRACK:/ALERT crack:/')"$'\n'
    fi
  fi

  # 5. Finisher churn: a module re-dispatched after it already reported itself drained.
  for f in "$SP"/wlog/finisher_ov*; do
    [ -f "$f" ] || continue
    a=$(cat "$f" 2>/dev/null); case "$a" in ''|*[!0-9]*) continue ;; esac
    if [ "$a" -ge 2 ] && [ "$a" -lt 8 ] && [ "$(mins_since $(mtime "$f"))" -lt 60 ]; then
      alerts="${alerts}ALERT finisher: $(basename "$f" | sed 's/finisher_//') on attempt ${a} (exclusive driver time, low yield)"$'\n'
    fi
  done

  # 5b. A DISPATCHER THAT IS UP BUT SERVING NOBODY. Check 1 only asks whether a dispatcher PROCESS
  # exists, and every other check here measures activity that a held fleet still has: the driver is
  # alive, the last commit is recent, the logs were touched. So the one state that costs a whole
  # afternoon -- pull_all holding on an unpromoted lever or a blocker class, with a full queue and
  # zero workers -- was watched by nothing. Measured 2026-09-08: idle 13:26 to 14:59, discovered by
  # hand. The hold itself is CORRECT and must stay; it is the silence that is the bug.
  _idlef="$SP/wlog/.zero_workers_since"
  _wn=$(ps_count --count --worker '\s-p\s.*DQIX')
  [ -z "$_wn" ] && _wn=0
  _pw=$(ps_count --count --match 'pull_worker')
  [ -z "$_pw" ] && _pw=0
  if [ "$_wn" -gt 0 ] 2>/dev/null || [ "$_pw" -gt 0 ] 2>/dev/null; then
    rm -f "$_idlef"
  else
    [ -f "$_idlef" ] || date +%s > "$_idlef"
    _since=$(cat "$_idlef" 2>/dev/null); case "$_since" in ''|*[!0-9]*) _since=$(now) ;; esac
    _idle=$(( ( $(now) - _since ) / 60 ))
    if [ "$_idle" -ge "${HELD_MIN:-10}" ]; then
      _why=$(awk '/HOLDING/{h=$0} /claiming resumed|blocker class addressed/{h=""} END{print h}' "$SP/wlog/pull_all.log" 2>/dev/null)
      [ -z "$_why" ] && _why="no HOLDING line -- slots simply never refilled"
      alerts="${alerts}ALERT idle: dispatcher up, ZERO workers for ${_idle}m -- ${_why}"$'\n'
    fi
  fi

  # 6. Nothing being worked at all. Under the batch driver this meant "main starved", measured from
  # run_main.log. Pull dispatch does not write that file -- it picks each slot's module by remaining
  # pool size, so no module can starve by construction -- and checking the old path alerted forever
  # the moment dispatch changed. What still matters is whether ANY work is progressing, so take the
  # newest of either dispatcher's activity logs.
  t=$(mtime "$SP/wlog/run_main.log")
  for _pl in "$SP"/wlog/pull_*_s*.log; do
    [ -f "$_pl" ] || continue
    _t=$(mtime "$_pl"); [ "$_t" -gt "$t" ] && t=$_t
  done
  if [ "$t" -eq 0 ]; then
    alerts="${alerts}ALERT starve: no dispatcher activity logged at all"$'\n'
  else
    age=$(mins_since $t)
    [ "$age" -ge "$STARVE_MIN" ] && alerts="${alerts}ALERT starve: no worker activity for ${age}m"$'\n'
  fi

  # 6b. OUTCOME, not just liveness. Every other check here asks whether work is HAPPENING; none
  # asked whether it was SUCCEEDING, so a fleet that ran flawlessly and matched nothing looked
  # healthy. That is not hypothetical: moving the recipes out of the worker doc put the large band
  # at 0-for-5 for $11 and the monitor said OK throughout. pullstat distinguishes the two failure
  # shapes that matter -- quitting early (something is missing) versus hitting the cap (the budget
  # truncated real work).
  _po=$(python "$KIT/pullstat.py" --alerts 2>/dev/null)
  [ -n "$_po" ] && alerts="${alerts}${_po}"$'\n'

  # 7. Red gate.
  for f in "$SP"/wlog/fw_*.log "$SP"/wlog/gate_*.txt; do
    [ -f "$f" ] || continue
    [ "$(mins_since $(mtime "$f"))" -gt 20 ] && continue
    if tail -40 "$f" 2>/dev/null | grep -qE "RED:|sha1 mismatch|not found in linked binary|FAILED:"; then
      alerts="${alerts}ALERT gate: $(basename "$f") -- $(tail -40 "$f" | grep -oE 'RED:.*|sha1 mismatch.*|Symbol .* not found.*' | tail -1 | cut -c1-100)"$'\n'
    fi
  done

  # 8. Invariant break.
  if [ -f "$SP/wlog/selfcheck.txt" ] && [ "$(mins_since $(mtime "$SP/wlog/selfcheck.txt"))" -lt 60 ]; then
    grep -q '^FAIL' "$SP/wlog/selfcheck.txt" 2>/dev/null && \
      alerts="${alerts}ALERT invariant: $(grep '^FAIL' "$SP/wlog/selfcheck.txt" | head -1 | cut -c1-100)"$'\n'
  fi

  # De-duplicate: each distinct alert at most once per REPEAT_MIN.
  if [ -n "$alerts" ]; then
    fresh=""
    while IFS= read -r line; do
      [ -z "$line" ] && continue
      key=$(printf '%s' "$line" | sed 's/[0-9]\+/N/g' | md5sum | cut -d' ' -f1)
      prev=$(grep "^$key " "$STATE" 2>/dev/null | tail -1 | awk '{print $2}')
      if [ -z "$prev" ] || [ $(( ( $(now) - prev ) / 60 )) -ge "$REPEAT_MIN" ]; then
        fresh="${fresh}${line}"$'\n'
        grep -v "^$key " "$STATE" 2>/dev/null > "$STATE.tmp"; mv -f "$STATE.tmp" "$STATE" 2>/dev/null
        echo "$key $(now)" >> "$STATE"
      fi
    done <<< "$alerts"
    alerts="$fresh"
  fi

  if [ -n "$alerts" ]; then
    printf '%s' "$alerts"
    last_ok=0
  elif [ $(( $(now) - last_ok )) -ge 1800 ]; then
    w=$(ps_count --count --worker '\s-p\s.*DQIX')
    echo "OK $(date '+%H:%M') cov $(cov_now) · workers ${w:-0} · last commit ${stalled}m ago"
    # PERIODIC RESULT SUMMARY, not just alerts. Everything else here fires only when something is
    # WRONG, so a fleet running well produces silence -- which answers "is it broken?" but never
    # "is it working, and at what cost?". Every SUMMARY_EVERY cycles, publish the band table so the
    # success rate and $/function arrive without being asked for.
    # TIME-BASED, NOT CYCLE-COUNTED. The counter only advanced on clean cycles and reset whenever
    # the monitor restarted, so the 30-minute summary printed once at 02:06 and then never again --
    # the one channel meant to deliver results without being asked was silently not delivering.
    # A timestamp file survives restarts and does not care how many cycles were alert-free.
    _last=$(cat "$SP/wlog/.last_summary" 2>/dev/null); _last=${_last:-0}
    if [ $(( $(now) - _last )) -ge "${SUMMARY_SECS:-1800}" ]; then
      echo "$(now)" > "$SP/wlog/.last_summary"
      python "$KIT/pullstat.py" 2>/dev/null | sed 's/^/    /' | grep -vE '^\s*$'
    fi
    last_ok=$(now)
  fi
  sleep "$INTERVAL"
done
