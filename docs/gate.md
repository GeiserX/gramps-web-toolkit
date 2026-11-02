# gwt-gate

FamilySearch and similar sites block an account or an IP address after too many requests, and the block lasts a day. When several agents and scripts call the same site from one machine, each one pacing itself is not enough: together they cross the limit. `gwt-gate` gives them one common pace. Every lane asks the gate before each request, and the gate hands out one slot at a time for all of them together.

Standard library only, Python 3.8 or newer, on macOS or Linux only: it locks a file with `fcntl`, which Windows does not have.

## How the pace moves

- **Start slow.** The first gap between two requests, for all lanes together, is `start` seconds (default 15).
- **Speed up while quiet.** After `step_after` seconds without a hit (default one hour), the gap shrinks by a fifth, never below `floor` (default 8 s).
- **Back off hard on a hit.** A 429, a block page or a security notice is a hit (a captcha a person must solve goes to `human`, below): every lane pauses for `pause` seconds (default 20 minutes) and the gap triples, never above `ceil` (default 60 s).
- **Paused lanes do other work.** While the pause has more than `max_wait` seconds left (default 2 minutes), `wait` does not sleep: it exits with code 3 at once, and the lane goes on with sources that do not need this site. A shorter pause is waited out.
- **A block stops everything.** `block <hours> <why>` parks the gate, for an account suspension or an IP block.
- **Every pause carries its reason.** `wait`, `status` and the `Paused` exception show the text of the hit, `dead` or `block` that started the pause. When the pause ends, so does its reason, so an old block never shows up as the cause of a later pause.
- **`clear <reason>` ends a pause, a block, a parking for a person and every expired account now** and logs a `CLEAR` line with your reason and the one it replaced. Use it instead of editing `state.json` or `calls.log` by hand.

## Which answer goes to which verb

| The site answers | Verb | Pause | Gap | Exit |
|---|---|---|---|---|
| 429, a block or security page | `hit` | `pause` seconds | triples, up to `ceil` | 0 |
| a captcha only a person can solve | `human` | until cleared, every lane | triples, up to `ceil` | 0 |
| 403 on one record (no permission to see it) | `denied` | none | unchanged | 0 |
| 401 (the session is dead) | `dead` | `pause` seconds | unchanged | 4 |
| 401 for one account, when lanes name their account | `auth-expired` | until the session file changes, that account only | unchanged | 4 |
| a notice that the account or IP is blocked | `block <hours>` | that many hours | unchanged | 0 |

A 403 that refuses one record and a 401 that ends the session say nothing about the request rate, so they do not slow the pace. `denied` only writes a `DENIED` line, so you can count refusals later. `dead` still pauses every lane, because no lane can work until someone logs in again, but it leaves the gap where it was and never shortens a longer pause or block already running, nor replaces its reason: a 401 during a 6-hour block logs `pause kept` and the block keeps its own text. If a 403 comes back as a block page rather than a refusal of one record, it is a `hit`.

The defaults come from one IP that FamilySearch blocked after a few hundred requests at one every 5 to 6 seconds ([hard-won.md](hard-won.md)): they start well below that pace, and a gate that speeds up only finds the limit by crossing it.

## Priority lanes

Some work is more urgent than the rest. Lanes named in `priority` go first: a priority lane that is waiting claims the next slot, and keeps the claim for 1.5 gaps after its call, so another lane does not take the slot while it reads the answer. Other lanes still get every slot no priority lane claims. During a long priority run, after `share` priority calls in a row (default 3) the next slot is open to any lane, so the rest slow to one slot in `share`+1 instead of stopping.

## Captchas: hand them to a person at once

A captcha does not go away on a timer. A lane that pauses for 20 minutes and tries again meets the same captcha, and a person learns about it hours later, if at all. `human` parks every lane until someone solves it, and tells that person straight away:

```sh
export GWT_GATE_NOTIFY='ntfy publish my-topic "{site}: {what} at {url} ({lane})"'
gate/gwt-gate human my-lane 'hCaptcha on the search page' --url 'https://records.example.org/search?q=smith'
gate/gwt-gate wait-clear --probe 'curl -fsS -o /dev/null https://records.example.org/search?q=test' --every 60 --timeout 3600
```

- **The notice runs once per parking.** `--notify` (or `GWT_GATE_NOTIFY`) is a command template. `{site}` (the name of the gate directory), `{lane}`, `{url}` and `{what}` are filled in. Any program works: `osascript` for a macOS dialog, `ntfy`, `notify-send`, a script of your own. A second `human` while the gate is already parked only writes a log line, so ten lanes meeting the same wall send one notice.
- **No shell.** The template is split into words first, and each word is filled in on its own, so a `{what}` or `{url}` copied from a site stays one argument and can never start a second command. Use a script of your own if you need pipes. Write a literal brace as `{{` or `}}`, for example in a JSON payload. A template that cannot be split or filled exits 2 before anything is parked. A notice that fails (a non-zero exit, a missing program) is logged as `NOTIFY ... exit=`, and `human` still parks the gate but says so on stderr and exits 1, since nobody was told.
- **Parked means parked.** While parked, `wait` exits 3 for every lane, and `Paused.left` is `gwt_gate.PARKED` (infinity), whatever `--max-wait` says. `status` shows the `human` entry: lane, what, URL and when.
- **`wait-clear --probe CMD`** runs a control command every `--every` seconds (default 60) and ends the parking the first time it exits 0. The probe is your own request that only succeeds on a normal answer, for example a known search that must return a known record. If the probe still fails after `--timeout` seconds (default one hour), the notice runs once more with `still waiting:` in front of `{what}`. After a second timeout it exits 5 with the gate still parked. It returns at once if someone has already run `clear`. A passing probe ends the parking only: a `block` or a pause that is running keeps running, since a page that answers says nothing about an account being reinstated. The probe runs without a shell too, and it does not take a gate slot, so keep it to one request. A probe that cannot run at all (the program does not exist) stops `wait-clear` at once with exit 2 and the gate still parked.
- `clear <reason>` ends a parking by hand, like a pause.
- **The gap stays tripled after the parking ends**, as after a `hit`: a captcha usually means the pace was too fast. It shrinks by a fifth after each `step_after` seconds without a hit, down to `floor`.

### Which wall is it?

`challenge(body, url)` names the bot wall in an answer: `perfdrive` (Radware), `imperva` (Incapsula), `cloudflare`, `hcaptcha` or `recaptcha`, or `None`. A reader should call it on every answer it did not expect; `gwt-read` does not do this yet and still reports a captcha with `hit`. A page that only uses the word "captcha" is not a wall, and neither is an ordinary page that loads Cloudflare's `/cdn-cgi/challenge-platform` script. A reCAPTCHA or hCaptcha widget does count, so call it on answers you did not expect, not on a results page that also carries a form with a captcha. From a shell, `gwt-gate challenge saved.html` (or `-` for stdin) prints the vendor and exits 0, prints `none` and exits 1, or exits 2 when it cannot read the file.

## One account signed out

`dead` pauses every lane for `pause` seconds, which is right when the whole gate is one account. When lanes on one gate use several accounts, a 401 on one account should stop only that account, and it should end when someone signs in again, not after a fixed 20 minutes:

```sh
gate/gwt-gate auth-expired my-lane /records --account acct-1 --session ~/.config/mysite/acct-1.token
gate/gwt-gate wait my-lane --account acct-1   # exit 3 until the token file changes
gate/gwt-gate wait other-lane                 # lanes that name no account, or another one, go on
```

`auth-expired` exits 4, like `dead`. It leaves the gap and the pause alone, since an expired session says nothing about the request rate. The account resumes on its own the next time one of its lanes calls `wait` after the session file's modification time changed, and a line `AUTH <account> session renewed` goes to the log. A lane that does not pass `--account` is never parked by it. The session path is stored in full, so a relative `--session` names the same file for lanes started in other directories. If a sign-in never rewrites that file (a browser cookie jar, a token written somewhere else), `clear <reason>` releases every expired account and names them in its `CLEAR` line.

## Limits a site publishes

Some sites say how fast you may go: a `Crawl-delay` in `robots.txt`, or a rate in their API terms. `gate/profiles.tsv` keeps these, one row per host, each with the page it came from:

| Column | Meaning |
|---|---|
| `host` | the host name, as in the URL |
| `per_hour` | at most this many requests in any hour; empty for none |
| `burst`, `cooldown_s` | at most `burst` requests in any `cooldown_s` seconds. A `Crawl-delay: 10` is burst 1, cooldown 10 |
| `fresh_profile` | `yes` when the site wants a new browser profile for every query. The gate cannot do that; it is there so you plan for it |
| `notes` | where the limit is published, with its URL and the date it was checked |

```sh
gate/gwt-gate --profile www.loc.gov wait my-lane
GWT_GATE_PROFILE=geneteka.genealodzy.pl gate/gwt-gate wait my-lane
gate/gwt-gate --per-hour 100 --burst 1 --cooldown 5 wait my-lane   # a host with no row
```

The site's limits come on top of the gate's own pace: a call waits for whichever is later. When a limit has more than `max_wait` seconds to run, `wait` exits 3 at once with the reason (`the site allows 20 requests every 60 s`), like a pause. `--profile` accepts a URL too, with or without a port. `--profiles FILE` (`GWT_GATE_PROFILES`) reads your own file. `--per-hour`, `--burst` and `--cooldown` (`GWT_GATE_PER_HOUR`, `GWT_GATE_BURST`, `GWT_GATE_COOLDOWN`) beat the profile. A host with no row is an error (exit 2), never a silent "no limit": add a row, or drop `--profile` and pass the flags. So is a `per_hour` or `burst` below 1 in a row or a flag: leave the cell empty for no limit. Every process on the gate directory must use the same profile, since only calls made with a limit are counted against it.

Only add a row for a limit the site itself publishes, with its URL. A limit you discovered by being blocked belongs in [Hard-won lessons](hard-won.md) and in your own profiles file.

## How long will it take?

`gwt-gate eta <n>` prints how many seconds `n` more requests take at the current gap, the site's limits and the pause that is running: `{"requests": 40, "eta_s": 590, "parked": false}`. While the gate is parked for a person, `eta_s` is `null`.

## Sessions that die

A 401 means the session is dead. The rule: report it with `dead` and stop with exit code 4, never hand the caller an empty answer as if it were the result. A long-running worker that read its token once should first look at the token file again: if it changed since (someone logged in again), retry once with the new token.

```python
from gwt_gate import Gate, Paused, reread_token

gate = Gate('~/.cache/gwt-gate', priority={'urgent'})
try:
    gate.wait('my-lane')
except Paused as exc:
    ...                                   # exc.left seconds to go, exc.why: do other work
status, body = fetch(url, token)
if status == 429:
    gate.hit('my-lane', f'HTTP 429 {url}')
elif status == 403:
    gate.denied('my-lane', f'HTTP 403 {url}')  # no permission for this record: skip it
elif status == 401:
    fresh = reread_token(token_file, token)
    if fresh:
        token = fresh                     # retry once with it
    else:
        gate.dead('my-lane', f'HTTP 401 session dead {url}')
        raise SystemExit(4)
```

## As a CLI

```sh
export GWT_GATE_DIR=~/.cache/gwt-gate GWT_GATE_PRIORITY=urgent

gate/gwt-gate wait my-lane          # before every request; exit 3 while paused
gate/gwt-gate hit my-lane 'HTTP 429 /search'
gate/gwt-gate denied my-lane '/record/1'  # a 403 on one record: logged, no pause
gate/gwt-gate dead my-lane '/records'     # a 401: every lane pauses, the gap stays, exit 4
gate/gwt-gate block 24 'account suspended'
gate/gwt-gate clear 'block lifted'        # end the pause or block now
gate/gwt-gate status                # {"gap_s": 15.0, "paused_for_s": 0, "why": "", "last_hit": ""} and the last calls
gate/gwt-gate human my-lane 'hCaptcha' --url URL   # park every lane, notify a person once
gate/gwt-gate wait-clear --probe 'CMD'    # end the parking when CMD exits 0; exit 5 if it never does
gate/gwt-gate auth-expired my-lane --account A --session FILE   # park one account until FILE changes, exit 4
gate/gwt-gate eta 40                # seconds 40 more requests take
gate/gwt-gate challenge page.html   # imperva, cloudflare, ... or none (exit 1); exit 2 if unreadable
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
| `--notify 'CMD {site} {lane} {url} {what}'` | `GWT_GATE_NOTIFY` | none |
| `--profile HOST` | `GWT_GATE_PROFILE` | none |
| `--profiles FILE` | `GWT_GATE_PROFILES` | `gate/profiles.tsv` |
| `--per-hour`, `--burst`, `--cooldown` | `GWT_GATE_PER_HOUR`, `GWT_GATE_BURST`, `GWT_GATE_COOLDOWN` | none |

A flag beats the environment, which beats the default. Every process pointed at the same directory shares the pace; pass the same settings to all of them, or wrap the CLI in a small script of your own that fixes them.

## What it keeps

The directory holds `state.json` (the gap, the last call, the pause, the priority claim, a parking for a person, the accounts whose session expired and, with a limit set, the times of the last hour's calls; written by atomic rename under the lock), `lock` (the `flock` every read and write of the state takes) and `calls.log`, one line per call, hit, denial, dead session, block, clear, parking (`HUMAN`), notice (`NOTIFY`), expired or renewed session (`AUTH-EXPIRED`, `AUTH`) and pace change, so the limit you find is written down. Nothing in it is secret, but the log says when and how often you called the site.

## Traps

- **One gate per site and machine, not per lane.** A pace per lane multiplies by the number of lanes.
- **The cache goes before the gate.** A request answered from [gwt-fscache](fscache.md) costs nothing and should not wait for a slot. Call `wait` in the miss path only.
- **A "no result" while the site was blocking is not a negative.** Put it back on the list.
- **Do not dodge a block by moving to another IP.** The block is on the behaviour, and the next IP goes the same way.

## Tests

```sh
cd gate && python3 -m unittest test_gwt_gate
```

The tests run the gate on a fake clock: lanes share one gap, a hit pauses everyone and triples the gap up to the ceiling, the gap shrinks after a quiet hour down to the floor, a priority claim holds other lanes back until `share` priority calls in a row open the slot, a block carries its reason, `wait` and `status` show the text of the hit and drop it once the pause is over, `status` drops a reason once its pause has run out even with no `wait` in between, `dead` pauses without moving the gap and never shortens or relabels a block, `denied` changes neither the pause nor the gap, `clear` ends a pause and logs why, the gap is rounded in the hit and call lines of the log, an existing state file is kept, and a refreshed token file is picked up after a 401. `human` parks every lane with no timer and runs a test notify script once per parking, filling each word alone with no shell; `wait-clear` ends the parking when its probe passes, reminds once after the timeout and then gives up still parked; `auth-expired` parks one account without moving the gap (a `hit` does), stores the session path whole so a lane in another directory stays parked, and lets it go when the session file changes or on `clear`; a notice or a probe that cannot run, or a template that cannot be filled, is reported with a non-zero exit; a per-hour cap refuses the next request in the hour and a burst spaces calls by its cooldown, keeping only that window of call times; `eta` adds the pause and the limits; `challenge` names the vendor in saved wall pages, ignores a page that only says "captcha" or loads Cloudflare's script, and exits 2 on a file it cannot read; the shipped profiles load and each cites a URL. A smoke run drives a fake reader through a captcha and back with real processes. `gate/mutants.txt` removes each of these guards and checks a test fails. Two real processes share one directory, and the CLI exits 3 while paused and 4 on a dead session, and its `status`, `denied` and `clear` work end to end.
