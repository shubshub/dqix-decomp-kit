#!/bin/bash
# FULL STOP, in two tiers, because the two kinds of job cost different things.
#
#   bash fullstop.sh          stop TOKEN SPEND immediately; let CPU-only jobs finish
#   bash fullstop.sh --hard   also kill the CPU-only jobs (integration, sweeps)
#   bash fullstop.sh --dry    report what each tier would do, kill nothing
#
# TIER 1 -- SPENDERS, KILLED IMMEDIATELY. Headless `claude.exe -p` workers spend money every second
# they live, and every driver/supervisor is a spender too because its whole job is to launch more of
# them. They all die in ONE PowerShell pass rather than a sequence: killing the supervisor first and
# the workers last leaves a window in which the dying driver starts a fresh worker, and a detached
# claude.exe survives a POSIX kill, gets reparented to PID 1, and keeps burning with nothing left to
# collect its output. After the pass we re-check twice, because that race is real.
#
# TIER 2 -- CPU ONLY, ALLOWED TO FINISH. finish_wave, the integrators and the sweeps cost electricity
# and nothing else, and killing them is actively harmful: a finish_wave killed between its clean and
# restore steps empties src/ and strands every matched file it was landing. Default is to report
# them and leave them alone. --hard kills them, and then `git -C <repo> checkout -- src/` may be
# needed.
#
# NEVER touches interactive `claude.exe` sessions -- a worker is identified by ` -p ` on its command
# line. The operator has unrelated Claude sessions open.
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && { pwd -W 2>/dev/null || pwd; })"
# The interpreter this kit is running under. A bare `python` is python3 under Git Bash on
# Windows and does not exist at all on a Debian that keeps its packages in a virtualenv, so
# every call below asks the kit which interpreter to use instead of assuming one.
PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
# ninja and anything else installed beside it live in the same directory, and they are on
# PATH only while the venv is ACTIVATED. Put them there for this script's children.
export PATH="$(dirname "$PY"):$PATH"
SP="$("$PY" "$KIT/kitpaths.py" state)"
REPO="$("$PY" "$KIT/kitpaths.py" repo)"
MODE="${1:-soft}"
DRY=0
[ "$MODE" = "--dry" ] && DRY=1
HARD=0
[ "$MODE" = "--hard" ] && HARD=1

# Anything whose purpose is to run, or to launch, a paid session.
SPENDERS='supervise\.sh|run_all\.sh|run_module\.sh|run_overlay\.sh|run_main\.sh|pull_all\.sh|pull_fleet\.sh|pull_worker\.sh|restart_pull\.sh|resume_sweep\.sh|resume_one\.sh|smoke\.sh|watchdog\.sh|limit_guard\.sh'
# Local compute only: no API calls, no worker sessions. DERIVED, NOT ENUMERATED -- anything running
# out of the scratchpad that is not a spender and not a watcher is CPU work. This was a hand-kept
# list of script names for months, and a hand-kept list does not grow when a script is added: it
# never learned `skipsweep.py` or `poolsweep.py`, so two consecutive stops printed "TIER 2 none
# running" with both alive. 67 of the ~100 scripts in $SP were outside every tier. A path match
# cannot go stale.
# A path match still misses a job started from INSIDE $SP with a bare relative name: four
# `"$PY" -u repairsweep.py` drivers survived a --hard stop on 2026-09-05, immediately respawned
# their colorsweep children, and the kill line reported success. So match the script BASENAMES too,
# read off disk for the same reason the path match exists -- a list read at run time cannot go stale.
_cpunames=$(ls "$KIT"/*.py "$KIT"/*.sh 2>/dev/null | xargs -n1 basename 2>/dev/null \
            | sed 's/\./\\./g' | paste -sd'|' -)
CPUJOBS="dqix.sp|handwork.evo|permuter\.py${_cpunames:+|$_cpunames}"
# Monitors and their children. These spend nothing, but a stop that leaves them running is not a
# stop the user can see: on 2026-08-25 this script printed "none running" for both tiers while two
# `bash health.sh` monitors -- one an orphan from a session that had already died -- and eight
# `tail`/`grep` watchers were alive, which is exactly what the user was still looking at. TaskStop on
# a Monitor ends the task, never the process it spawned, so nothing else cleans these up.
# EVERY watcher must be listed or a stop leaves it running and a restart stacks another copy on top:
# three presweep_watch loops accumulated this way in one minute.
WATCHERS='health\.sh|pull_watch\.sh|watch_verdicts\.sh|leverwatch\.sh|nearmiss_watch\.sh|presweep_watch\.sh|verdictwatch\.sh'
# Subtracted from TIER 2, because those run out of $SP too and are killed by their own tiers. It has
# to be assigned AFTER both halves exist: written above WATCHERS it expanded to a trailing `|`, and
# an empty alternative matches every string, so `-notmatch` excluded the entire process table and
# TIER 2 reported "none running" no matter what was live.
NOTCPU="$SPENDERS|$WATCHERS"

# NEVER match this session's own tool calls. A Bash tool invocation carries the command text
# verbatim on its command line, so merely MENTIONING resume_sweep.sh in a command made that shell
# look like a driver -- and this script would have killed the shell it was running in. Every Claude
# Code shell sources a shell-snapshot, which is the reliable tell.
# IT WAS DECLARED AND NEVER USED. Until 2026-08-25 this variable appeared exactly once, on its own
# definition line: every filter below matched this session's shells too, which is why the counts it
# printed were never trustworthy in either direction.
# `Win32_Process` is in the text of every query below, so once TIER 2 became a PATH match the
# powershell process running the query matched itself and became its own first casualty.
EXCL='shell-snapshots|Win32_Process|fullstop\.sh'

PQ() { "$PY" "$KIT/procq.py" "$@" 2>/dev/null | tr -d '\r'; }

ps_list() {   # $1 = regex over the command line; $2 = extra regex to EXCLUDE (optional)
  PQ --list --match "$1" --notmatch "${2:-__never__}" --notmatch "$EXCL"
}

kill_spenders() {   # one pass: workers AND everything that launches them
  PQ --kill --worker '\s-p\s.*DQIX' --match "$SPENDERS" --notmatch "$EXCL"
}

count_workers() {
  PQ --count --worker '\s-p\s.*DQIX' --match "$SPENDERS" --notmatch "$EXCL"
}

# A Monitor's `cd` runs in the wrapper bash, so its tail/grep children carry no path of their own.
WATCHKIDS='tail[^ ]* -f|--line-buffered'
# EXCL's `shell-snapshots` guard hides EVERY watcher: a Monitor runs its script through the same
# Claude Code shell wrapper, so the guard meant to spare this session's own shell spared all of them
# and --monitors reported "none running" with four health.sh processes live. The $self pid set below
# protects the caller's own chain exactly, so the watcher tier does not need the text guard.
EXCLW='Win32_Process|fullstop\.sh'

WATCH=(--watchers "$WATCHERS" --exclw "$EXCLW" --kids "$WATCHKIDS" --ours 'wlog|dqix-sp|dqix-decomp')

kill_watchers() {   # monitors, and the tail/grep children a Monitor leaves behind
  PQ --kill "${WATCH[@]}"
}

count_watchers() {
  PQ --count "${WATCH[@]}"
}

list_watchers() {
  PQ --list "${WATCH[@]}"
}

# TaskStop ends a Monitor task and leaves its bash/tail/grep running, so restarting a watch a few
# times leaks a loop each time -- five live health.sh loops accumulated in one session, each waking
# every 120s. Reaping them must not require stopping the fleet, or the choice becomes "leak, or
# throw away a worker mid-function".
if [ "$MODE" = "--monitors" ]; then
  echo "MONITORS ONLY $(date '+%H:%M:%S')"
  _w=$(count_watchers)
  [ "${_w:-0}" -eq 0 ] && { echo "  none running"; exit 0; }
  echo "  killed: $(kill_watchers)"
  exit 0
fi

echo "FULL STOP $(date '+%H:%M:%S')${DRY:+}"
[ "$DRY" -eq 1 ] && echo "  (dry run -- nothing will be killed)"

# Flags first and always: a loop that somehow survives re-reads these at its next cycle and exits,
# and they stop a later launch from starting into a stop that was meant to last.
if [ "$DRY" -eq 0 ]; then
  touch "$SP/FLEET_STOPPED" "$SP/STOP_PULL" "$SP/STOP_RESUME"
  echo "  flags set: FLEET_STOPPED STOP_PULL STOP_RESUME"
else
  echo "  would set: FLEET_STOPPED STOP_PULL STOP_RESUME"
fi

echo
echo "TIER 1 -- token spenders (immediate)"
_found=$(ps_list "(claude(\.exe)?\b.*\s-p\s.*DQIX)|$SPENDERS")
if [ -z "$_found" ]; then
  echo "  none running"
else
  echo "$_found" | awk -F'@@@' '{printf "  pid %-7s %3s min  %s\n", $1, $2, $3}'
  if [ "$DRY" -eq 0 ]; then
    n=$(kill_spenders); echo "  killed: ${n:-0}"
    # The race: a driver can spawn one more worker as it dies. Check twice before believing it.
    for _i in 1 2; do
      sleep 2
      left=$(count_workers)
      [ "${left:-0}" -eq 0 ] && break
      echo "  $left reappeared -- killing again"
      kill_spenders > /dev/null
    done
  fi
fi
left=$(count_workers)
echo "  spenders remaining: ${left:-0}"
[ "${left:-0}" -gt 0 ] && echo "  !! run: bash \"$SP/killfleet.sh\" --all"

echo
echo "TIER 2 -- CPU-only jobs"
_cpu=$(ps_list "$CPUJOBS" "$NOTCPU")
if [ -z "$_cpu" ]; then
  echo "  none running"
elif [ "$HARD" -eq 1 ] && [ "$DRY" -eq 0 ]; then
  echo "$_cpu" | awk -F'@@@' '{printf "  pid %-7s %3s min  %s\n", $1, $2, $3}'
  kill_cpu() {
    PQ --kill --match "$CPUJOBS" --notmatch "$NOTCPU" --notmatch "$EXCL"
  }
  echo "  killed: $(kill_cpu)"
  for _i in 1 2; do
    sleep 2
    _again=$(ps_list "$CPUJOBS" "$NOTCPU")
    [ -z "$_again" ] && break
    echo "  $(echo "$_again" | wc -l) reappeared -- killing again: $(kill_cpu)"
  done
  echo "  CHECK THE REPO: a finish_wave killed mid-flight empties src/."
  echo "    git -C $REPO status --short | head"
  echo "    git -C $REPO checkout -- src/      # if it shows mass deletions"
  # The lock check below reads $_cpu, which was captured BEFORE this kill. Left stale it reported
  # "locks left in place (a CPU job still holds them)" about jobs that no longer existed, and then
  # skipped the cleanup -- and a stale wave.lock blocks every later integration.
  _cpu=""
else
  echo "$_cpu" | awk -F'@@@' '{printf "  pid %-7s %3s min  %s   (left to finish)\n", $1, $2, $3}'
  echo "  These spend no tokens. Killing an integration pass loses matched work; let it land."
  echo "  Force with: bash \"$SP/fullstop.sh\" --hard"
fi

echo
echo "TIER 3 -- monitors and watcher children"
_w=$(count_watchers)
if [ "${_w:-0}" -eq 0 ]; then
  echo "  none running"
elif [ "$DRY" -eq 1 ]; then
  list_watchers | awk -F'@@@' '{printf "  pid %-7s %3s min  %s\n", $1, $2, $3}'
  echo "  ${_w} watcher process(es) would be killed"
else
  echo "  killed: $(kill_watchers)"
  echo "  (TaskStop ends a Monitor task but never the bash/tail/grep it spawned)"
fi

# Locks belong to whatever is still holding them. Clearing wave.lock while a finish_wave still runs
# is precisely the failure the lock exists to prevent -- two waves deleting each other's files.
if [ "$DRY" -eq 0 ] && [ -z "$_cpu" ]; then
  rm -f "$SP/run_all.lock" 2>/dev/null
  # `rmdir` only removes an EMPTY directory and a live wave.lock always holds its owner's pid file,
  # so this cleanup could never fire on a real lock -- the one case it exists for. A stale wave.lock
  # blocks every subsequent integration until someone removes it by hand.
  if [ -d "$SP/wave.lock" ]; then
    rm -rf "$SP/wave.lock" 2>/dev/null && echo "  cleared wave.lock"
  fi
elif [ -n "$_cpu" ]; then
  echo "  locks left in place (a CPU job still holds them)"
fi

echo
_wleft=$(count_watchers)
echo "STOPPED. token spend: $([ "${left:-0}" -eq 0 ] && echo "halted" || echo "STILL RUNNING (${left})")\
$([ "${_wleft:-0}" -eq 0 ] && echo "" || echo ", ${_wleft} watcher(s) STILL RUNNING")"
