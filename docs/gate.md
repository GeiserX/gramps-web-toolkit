# gwt-gate

FamilySearch and similar sites block an account or an IP address after too many requests, and the block lasts a day. When several agents and scripts call the same site from one machine, each one pacing itself is not enough: together they cross the limit. `gwt-gate` gives them one common pace. Every lane asks the gate before each request, and the gate hands out one slot at a time for all of them together.

Standard library only, Python 3.8 or newer, on macOS or Linux (it locks a file with `fcntl`).

## How the pace moves

- **Start slow.** The first gap between two requests, for all lanes together, is `start` seconds (default 15).
- **Speed up while quiet.** After `step_after` seconds without a hit (default one hour), the gap shrinks by a fifth, never below `floor` (default 8 s).
- **Back off hard on a hit.** A 429, a 403, a captcha or a security notice is a hit: every lane pauses for `pause` seconds (default 20 minutes) and the gap triples, never above `ceil` (default 60 s).
- **Paused lanes do other work.** While the pause has more than `max_wait` seconds left (default 2 minutes), `wait` does not sleep: it exits with code 3 at once, and the lane goes on with sources that do not need this site. A shorter pause is waited out.
- **A block stops everything.** `block <hours> <why>` parks the gate, for an account suspension or an IP block.

The defaults come from one IP that FamilySearch blocked after a few hundred requests at one every 5 to 6 seconds ([hard-won.md](hard-won.md)): they start well below that pace, and a gate that speeds up only finds the limit by crossing it.

## Priority lanes

Some work is more urgent than the rest. Lanes named in `priority` go first: a priority lane that is waiting claims the next slot, and keeps the claim for 1.5 gaps after its call, so another lane does not take the slot while it reads the answer. Other lanes still get every slot no priority lane claims. During a long priority run, after `share` priority calls in a row (default 3) the next slot is open to any lane, so the rest slow to one slot in `share`+1 instead of stopping.

## Sessions that die

A 401 means the session is dead. The rule: report it as a hit and stop with exit code 4, never hand the caller an empty answer as if it were the result. A long-running worker that read its token once should first look at the token file again: if it changed since (someone logged in again), retry once with the new token.

```python
from gwt_gate import Gate, Paused, reread_token

gate = Gate('~/.cache/gwt-gate', priority={'urgent'})
try:
    gate.wait('my-lane')
except Paused as exc:
    ...                                   # exc.left seconds to go, exc.why: do other work
status, body = fetch(url, token)
if status in (403, 429):
    gate.hit('my-lane', f'HTTP {status} {url}')
elif status == 401:
    fresh = reread_token(token_file, token)
    if fresh:
        token = fresh                     # retry once with it
    else:
        gate.hit('my-lane', f'HTTP 401 session dead {url}')
        raise SystemExit(4)
```

## As a CLI

```sh
export GWT_GATE_DIR=~/.cache/gwt-gate GWT_GATE_PRIORITY=urgent

gate/gwt-gate wait my-lane          # before every request; exit 3 while paused
gate/gwt-gate hit my-lane 'HTTP 429 /search'
gate/gwt-gate dead my-lane '/records'     # a 401: a hit, then exit 4
gate/gwt-gate block 24 'account suspended'
gate/gwt-gate status                # {"gap_s": 15.0, "paused_for_s": 0} and the last calls
```

| Flag | Environment | Default |
|---|---|---|
| `--dir` | `GWT_GATE_DIR` | `~/.cache/gwt-gate` |
| `--priority a,b` | `GWT_GATE_PRIORITY` | none |
| `--share` | `GWT_GATE_SHARE` | 3 |
| `--start`, `--floor`, `--ceil` | `GWT_GATE_START`, `GWT_GATE_FLOOR`, `GWT_GATE_CEIL` | 15, 8, 60 s |
| `--step-after` | `GWT_GATE_STEP_AFTER` | 3600 s |
| `--pause` | `GWT_GATE_PAUSE` | 1200 s |
| `--max-wait` | `GWT_GATE_MAX_WAIT` | 120 s |

A flag beats the environment, which beats the default. Every process pointed at the same directory shares the pace; pass the same settings to all of them, or wrap the CLI in a small script of your own that fixes them.

## What it keeps

The directory holds `state.json` (the gap, the last call, the pause and the priority claim; written by atomic rename under the lock), `lock` (the `flock` every read and write of the state takes) and `calls.log`, one line per call, hit, block and pace change, so the limit you find is written down. Nothing in it is secret, but the log says when and how often you called the site.

## Traps

- **One gate per site and machine, not per lane.** A pace per lane multiplies by the number of lanes.
- **The cache goes before the gate.** A request answered from [gwt-fscache](fscache.md) costs nothing and should not wait for a slot. Call `wait` in the miss path only.
- **A "no result" while the site was blocking is not a negative.** Put it back on the list.
- **Do not dodge a block by moving to another IP.** The block is on the behaviour, and the next IP goes the same way.

## Tests

```sh
cd gate && python3 -m unittest test_gwt_gate
```

The tests run the gate on a fake clock: lanes share one gap, a hit pauses everyone and triples the gap up to the ceiling, the gap shrinks after a quiet hour down to the floor, a priority claim holds other lanes back until `share` priority calls in a row open the slot, a block carries its reason, an existing state file is kept, and a refreshed token file is picked up after a 401. Two real processes share one directory, and the CLI exits 3 while paused and 4 on a dead session.
