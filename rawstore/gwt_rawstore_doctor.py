"""gwt-rawstore doctor: a read-only health check of a research repo's disk use.

It reports what makes a repo that many agents read and commit to slow down the machine: too many packs, no
multi-pack-index, lazy fetching left on in a partial clone, binaries tracked in git, big scratch and cache folders
inside the work tree, and, on macOS, Time Machine backing all of it up. Each finding comes with the command that
fixes it. Exit status 0 when nothing is wrong, 1 when something is, 2 when it cannot run.

It never fetches. It runs only `git version`, `rev-parse`, `count-objects -v`, `config --get`, `ls-files` and, on
macOS, `tmutil isexcluded`; it reads no object, and every git it starts has GIT_NO_LAZY_FETCH=1 as a second guard.
"""
import json, os, re, subprocess, sys

GB = 10 ** 9
PACKS = 500           # default --max-packs
TMP_GB = 1.0          # default --max-tmp-gb: a scratch or cache folder, or a .git, bigger than this
BINARY_GB = 1.0       # default --max-binary-gb: binaries tracked in git, summed
MIDX_PACKS = 50       # more packs than this with no multi-pack-index is a finding
SCRATCH_DIRS = ('tmp', 'temp', 'scratch', '.tmp')
GEOMETRIC_PROMISOR = (2, 53)   # git repack --geometric handles promisor packs from 2.53 on
# a git that cannot fetch, prompt or take optional locks, whatever the caller's environment says
ENV = dict(GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0', LC_ALL='C')


class CannotRun(Exception):
    pass


def run(args, cwd, timeout=120):
    return subprocess.run(args, cwd=cwd, env=dict(os.environ, **ENV), capture_output=True, text=True,
                          timeout=timeout, check=False)


def git(cwd, *args):
    r = run(('git',) + args, cwd)
    return r.returncode, r.stdout, r.stderr


def truthy(v):
    return (v or '').strip().lower() in ('1', 'true', 'yes', 'on')


def gb(n):
    return '%.2f GB' % (n / GB) if n >= GB else '%.1f MB' % (n / 10 ** 6)


def tree_size(path):
    total = 0
    for d, dirs, files in os.walk(path):
        for name in files + [x for x in dirs if os.path.islink(os.path.join(d, x))]:
            try:
                total += os.lstat(os.path.join(d, name)).st_size
            except OSError:
                pass
    return total


def git_version(text):
    m = re.search(r'(\d+)\.(\d+)', text)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def doctor(repo='.', max_packs=PACKS, max_tmp_gb=TMP_GB, max_binary_gb=BINARY_GB, environ=None, platform=None):
    """Return the report: {repo, git, partial_clone, checks: [{check, level, message, fix}], findings}.
    level is ok, warn, info or skip; findings counts the warns. Raises CannotRun."""
    from gwt_rawstore import is_binary_name
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if not os.path.isdir(repo):
        raise CannotRun('no such directory: %s' % repo)
    try:
        rc, out, _ = git(repo, 'version')
    except FileNotFoundError:
        raise CannotRun('git is not installed') from None
    version = out.strip().removeprefix('git version ')
    rc, out, err = git(repo, 'rev-parse', '--show-toplevel', '--git-common-dir')
    if rc != 0:
        raise CannotRun('not a git work tree: %s (%s)' % (repo, err.strip().splitlines()[-1] if err.strip() else rc))
    top, common = out.splitlines()[:2]
    common = os.path.normpath(os.path.join(os.path.abspath(repo), common))
    checks = []

    def add(check, level, message, fix=''):
        checks.append({'check': check, 'level': level, 'message': message, 'fix': fix})

    # ---- packs and the multi-pack-index
    packdir = os.path.join(common, 'objects', 'pack')
    names = set(os.listdir(packdir)) if os.path.isdir(packdir) else set()
    packs = sorted(n for n in names if n.endswith('.pack'))
    promisor = sum(1 for n in packs if n[:-5] + '.promisor' in names)
    midx = 'multi-pack-index' in names or 'multi-pack-index.d' in names
    counts = dict(l.split(': ', 1) for l in git(repo, 'count-objects', '-v')[1].splitlines() if ': ' in l)
    git_bytes = sum(int(counts.get(k, 0)) for k in ('size', 'size-pack', 'size-garbage')) * 1024

    config = {}
    for key in ('gc.auto', 'maintenance.auto', 'extensions.partialclone'):
        config[key] = git(repo, 'config', '--get', key)[1].strip() or None
    filters = git(repo, 'config', '--get-regexp', r'^remote\..*\.partialclonefilter$')[1].split()[1::2]
    partial = bool(config['extensions.partialclone'] or filters or promisor)

    summary = '%d packs (%d promisor), %s in .git' % (len(packs), promisor, gb(git_bytes))
    if len(packs) > max_packs:
        fix = 'git repack --geometric=2 -d && git multi-pack-index write   ' \
              '(in the foreground, on a quiet disk, never while a backup runs)'
        if partial and git_version(version) < GEOMETRIC_PROMISOR:
            fix = 'upgrade git to 2.53 or newer first (older repack --geometric mishandles promisor packs), then ' + fix
        add('packs', 'warn', summary + '; over %d, every missing-object lookup reads every pack index' % max_packs, fix)
    else:
        add('packs', 'ok', summary)
    if len(packs) > MIDX_PACKS and not midx:
        add('multi-pack-index', 'warn', 'no multi-pack-index over %d packs' % len(packs), 'git multi-pack-index write')
    else:
        add('multi-pack-index', 'ok', 'present' if midx else 'not needed for %d packs' % len(packs))
    add('maintenance', 'info', 'gc.auto=%s maintenance.auto=%s' % (config['gc.auto'] or 'unset',
                                                                   config['maintenance.auto'] or 'unset'))

    # ---- lazy fetching in a partial clone
    if partial and not truthy(environ.get('GIT_NO_LAZY_FETCH')):
        add('lazy-fetch', 'warn', 'partial clone (filter %s) with lazy fetching on: git show <rev>:<path>, '
            'grep <rev>, archive, log -p and log -S download each missing blob as its own pack'
            % (','.join(filters) or 'unknown'),
            'export GIT_NO_LAZY_FETCH=1   (in every agent\'s environment; prefetch on purpose, in one batch)')
    else:
        add('lazy-fetch', 'ok', 'off' if partial else 'not a partial clone')

    # ---- binaries tracked in git: index only, sizes from the work tree
    by_folder = {}
    for path in git(top, 'ls-files', '-z')[1].split('\0'):
        if not path or not is_binary_name(path):
            continue
        try:
            size = os.lstat(os.path.join(top, path)).st_size
        except OSError:
            continue   # outside a sparse checkout: never read it from the object store
        folder = path.split('/', 1)[0] if '/' in path else '.'
        n, s = by_folder.get(folder, (0, 0))
        by_folder[folder] = (n + 1, s + size)
    binary_total = sum(s for _, s in by_folder.values())
    binary_files = sum(n for n, _ in by_folder.values())
    biggest = sorted(by_folder.items(), key=lambda kv: -kv[1][1])[:3]
    named = ', '.join('%s/ %s' % (f, gb(s)) for f, (_, s) in biggest)
    if binary_total > max_binary_gb * GB:
        add('binaries', 'warn', '%d tracked binaries, %s (%s)' % (binary_files, gb(binary_total), named),
            ' ; '.join('gwt-rawstore migrate %s --remove' % f for f, _ in biggest) + '   then commit the rawstore.tsv files')
    else:
        add('binaries', 'ok', '%d tracked binaries, %s' % (binary_files, gb(binary_total)))

    # ---- scratch and cache folders inside the work tree
    sized = {common: git_bytes}
    for name in SCRATCH_DIRS:
        p = os.path.join(top, name)
        if os.path.isdir(p) and not os.path.islink(p):
            sized[p] = size = tree_size(p)
            if size > max_tmp_gb * GB:
                add('scratch', 'warn', '%s/ holds %s' % (name, gb(size)),
                    'move scratch out of the repo (one mktemp -d per lane) and delete what is done')
            else:
                add('scratch', 'ok', '%s/ holds %s' % (name, gb(size)))
    caches = {}
    for d in [top] + [os.path.join(top, x) for x in sorted(os.listdir(top)) if x != '.git']:
        if os.path.isdir(d) and not os.path.islink(d) and os.path.isdir(os.path.join(d, '.cache')):
            caches[os.path.join(d, '.cache')] = '.cache folder'
    for k, v in sorted(environ.items()):
        if k.startswith('GWT_') and k.endswith(('_DIR', '_CACHE')) and v:
            p = os.path.realpath(os.path.expanduser(v))
            if p == top or p.startswith(top.rstrip('/') + '/'):
                caches[p] = '$' + k
    for p, why in sorted(caches.items()):
        sized[p] = size = tree_size(p)
        add('cache', 'warn', '%s inside the repo: %s (%s)' % (why, os.path.relpath(p, top), gb(size)),
            'keep caches under ~/.cache, outside the work tree' + (', and point %s there' % why if why[0] == '$' else ''))
    if not caches:
        add('cache', 'ok', 'no cache folder inside the repo')
    for folder, _ in biggest:
        if binary_total > max_binary_gb * GB and folder != '.':
            sized[os.path.join(top, folder)] = by_folder[folder][1]

    # ---- Time Machine (macOS only), for every folder above that is over --max-tmp-gb
    if platform != 'darwin':
        add('time-machine', 'skip', 'not macOS (%s): Time Machine checks skipped' % platform)
    else:
        big = [p for p, s in sorted(sized.items()) if s > max_tmp_gb * GB]
        included = []
        for p in big:
            try:
                r = run(('tmutil', 'isexcluded', p), top, timeout=30)
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                add('time-machine', 'skip', 'tmutil did not answer (%s)' % type(e).__name__)
                break
            if '[Excluded]' not in r.stdout:
                included.append(p)
        else:
            if included:
                add('time-machine', 'warn', 'backed up by Time Machine: ' + ', '.join(included),
                    ' ; '.join('tmutil addexclusion %s' % p for p in included) +
                    '   (for .git, only once the remote has every commit)')
            else:
                add('time-machine', 'ok', '%d big folder(s), all excluded' % len(big) if big else 'no folder over %s'
                    % gb(max_tmp_gb * GB))

    return {'repo': top, 'git': version, 'partial_clone': partial, 'checks': checks,
            'findings': sum(1 for c in checks if c['level'] == 'warn')}


def main(a, out=None):
    """a: argparse namespace with repo, json, max_packs, max_tmp_gb, max_binary_gb. Returns the exit status."""
    out = out or print
    try:
        rep = doctor(a.repo, a.max_packs, a.max_tmp_gb, a.max_binary_gb)
    except (CannotRun, subprocess.TimeoutExpired) as e:
        if a.json:
            out(json.dumps({'error': str(e)}))
        print('gwt-rawstore doctor: %s' % e, file=sys.stderr)
        return 2
    if a.json:
        out(json.dumps(rep, indent=2))
    else:
        out('gwt-rawstore doctor: %s (git %s%s)' % (rep['repo'], rep['git'], ', partial clone' if rep['partial_clone']
                                                     else ''))
        for c in rep['checks']:
            out('%-5s %s: %s' % (c['level'], c['check'], c['message']))
            if c['fix']:
                out('      fix: ' + c['fix'])
        out('%d finding(s)' % rep['findings'])
    return 1 if rep['findings'] else 0
