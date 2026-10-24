# gwt-spanish-poller

Certificates requested from Spanish civil registries through the Ministerio de Justicia sede come back slowly, and sometimes only through DEHú (Dirección Electrónica Habilitada única). The mail notice just says «Nueva comunicación electrónica», and «Estado de mis expedientes» drops a refused case without a word. Checking all of that by hand gets skipped, and a reply can sit unseen for days.

`gwt-spanish-poller` logs in as each configured person with their client certificate, takes a snapshot of their sede cases and their DEHú inbox, and compares it with the previous snapshot. It prints what changed and tells you through the exit code.

Node 18 or newer with Playwright (`npm install` in `spanish-poller/`, or point `NODE_PATH` at an existing `node_modules` that has `playwright`). Run it as [`spanish-poller/gwt-spanish-poller`](https://github.com/GeiserX/gramps-web-toolkit/blob/main/spanish-poller/gwt-spanish-poller). The code is in [`spanish-poller/gwt_spanish_poller.js`](https://github.com/GeiserX/gramps-web-toolkit/blob/main/spanish-poller/gwt_spanish_poller.js). Tests: `cd spanish-poller && node --test`.

```
gwt-spanish-poller --config FILE [--only ID] [--dump DIR]    # or set GWT_SPANISH_POLLER_CONFIG
```

## It only reads

Nothing is sent to the administration. The poller fills the sede search form (which only searches), lists DEHú notifications without opening them, and never accepts or rejects one: accepting a notification starts its legal clock, so that stays a human decision. A communication that is already readable is downloaded only when its sender or subject matches `download_senders`, and only once.

## What it reports

Per person it writes `<state_dir>/<id>.json` (the snapshot plus the events), `<state_dir>/<id>.txt` (the summary it also prints) and `<state_dir>/<id>.prev.json` (the snapshot before). Downloads go to `<state_dir>/<id>-downloads/`. When a login or a list fails, the page where it stopped (HTML, text and screenshot) is saved in `<state_dir>/<id>-errors/<time>/` even without `--dump`, and the summary says where. `--dump DIR` saves every page.

| Event | Meaning |
|---|---|
| `case_new` | A case appeared in «Estado de mis expedientes». |
| `case_status` | A case changed status, for example to «Cerrada - Resolución positiva». |
| `case_gone` | A case inside the search window vanished. The sede does that when it resolves or refuses a case, so look for the reply in DEHú and the mail. A case that only aged out of the window is not reported. |
| `notification_pending` | A DEHú notification waits to be accepted or rejected. Sender, subject and expiry are shown. It was not opened. |
| `notification_done` | A notification someone already accepted shows up in «realizadas». |
| `communication_new` | A new DEHú communication. |

Exit status: **0** nothing changed, **10** something changed (the first poll counts, as the baseline), **2** a login or a page failed and nothing else changed. A part that fails keeps its previous snapshot, so a broken login never reads as every case disappearing.

## Config

Keep the config, the state and the certificates outside this repo.

```json
{
  "state_dir": "~/poller-state",
  "download_senders": ["nacionalidad y estado civil", "dicireg"],
  "timeout_minutes": 25,
  "people": [
    { "id": "ana", "auth": { "kind": "pkcs12", "pfx": "~/certs/ana.p12", "passphrase_file": "~/certs/ana.pass" } },
    { "id": "luis", "auth": { "kind": "chrome" }, "sede_role": "Solicitante", "sede_days": 180 }
  ]
}
```

| Field | Default | What it does |
|---|---|---|
| `auth.kind` | | `pkcs12`: Playwright's Chromium presents the certificate from a `.p12` file. `chrome`: your installed Google Chrome, headless, picks the certificate from the system store. |
| `auth.pfx`, `auth.passphrase_file` / `auth.passphrase_env` | | The `.p12` file and its passphrase (from a file or an environment variable), for `pkcs12`. |
| `auth.chrome` | the macOS Chrome path | Chrome binary for `chrome`. |
| `auth.cert_hosts` | the Cl@ve and @firma hosts | Hosts that get the certificate in `pkcs12` mode. |
| `sede`, `dehu` | `true` | Set to `false` to skip one side. |
| `sede_role` | `Solicitante` | Search as `Solicitante`, `Inscrito` or `Ambos`. |
| `sede_days`, `sede_slice_days` | 180, 30 | How far back to search, and in slices of how many days. A slice the sede calls too big is split in half. |
| `timeout_minutes` | 25 | Per person. Past it the browser is closed and the run fails. |

### `chrome` mode needs a certificate policy

Headless Chrome cannot show the certificate chooser, so tell it which certificate to send to each Cl@ve host with the `AutoSelectCertificateForUrls` policy. On macOS (replace the CN with the certificate's subject):

```sh
defaults write com.google.Chrome AutoSelectCertificateForUrls -array \
  '{"pattern":"https://pasarela.clave.gob.es","filter":{"SUBJECT":{"CN":"APELLIDOS NOMBRE - 00000000T"}}}' \
  '{"pattern":"https://afirma.redsara.es","filter":{"SUBJECT":{"CN":"APELLIDOS NOMBRE - 00000000T"}}}'
```

Add the same entry for `pasarela-ident.clave.gob.es`, `se-pasarela.clave.gob.es`, `se-afirma.redsara.es` and `autentica.redsara.es`. The policy picks one certificate per host, so with several people in the keychain give all but one of them `pkcs12` mode.

## Traps it already handles

- **The sede and DEHú each get their own empty browser profile.** Cookies left by an earlier session made DEHú answer with empty responses, and with the sede's Cl@ve session still open the DEHú login kept landing back on its public page.
- **The Cl@ve certificate step sometimes resets the connection** (`ERR_CONNECTION_RESET`) for a minute or two. The DEHú login waits, goes back to the public page and logs in again, up to five times.
- **Cl@ve may skip its question.** When it still holds a session it hands straight back to the sede or DEHú without asking for the certificate; the login loop accepts both paths.
- **In `pkcs12` mode only the hosts that ask for a certificate are proxied.** Listing the sede and DEHú too made their pages fail now and then with `ERR_EMPTY_RESPONSE`. Those still happen at times on the DEHú hand-back, so it reloads.
- **DEHú is built from web components.** Its tables live inside shadow roots and its buttons inside closed components, so rows are read through every shadow root and «Detalles» is clicked by position.
- **DEHú shows a CAPTCHA after many logins in a short time** (we hit it after about a dozen test logins in one afternoon). The poller stops with a clear error. Poll once or twice a day, not in a tight loop.
- **Only the first page of each DEHú list is read** (10 rows). If there are more the summary says so instead of dropping them silently.
- **Every browser is closed by PID** when the run ends, on SIGTERM and SIGINT, and when the time budget runs out.

## Schedule it

Run it once a day and act on exit status 10. With cron:

```
30 8 * * * NODE_PATH=/path/to/node_modules /path/to/spanish-poller/gwt-spanish-poller --config ~/poller.json >> ~/poller.log 2>&1
```

On macOS use a LaunchAgent with `StartCalendarInterval` instead: `chrome` mode needs the logged-in user's keychain, which a LaunchAgent has and a LaunchDaemon does not.

## Keep the certificate and the secrets out of the repo

- Never commit the `.p12` or `.pfx` file or its passphrase. `.gitignore` already covers `*.p12`, `*.pfx` and `*.pem`; keep the certificate outside the repo anyway, as in the `~/certs/` example above.
- The poller reads the passphrase from one of two places: `auth.passphrase_env`, the name of an environment variable, or `auth.passphrase_file`, a file outside the repo with mode 600 (`chmod 600 ~/certs/ana.pass`). It never takes it on the command line.
