"""gwt-rawstore doctor against small repos built in a temp folder. Every repo, file and size here is invented."""
import argparse, io, json, os, shlex, subprocess, sys, tempfile, time, unittest
from contextlib import redirect_stderr
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gwt_rawstore_doctor as D
import gwt_rawstore as R

TINY = 1e-6   # GB: 1,000 bytes, so a 2,000-byte file is over it


def sh(cwd, *args, inp=None):
    return subprocess.run(('git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                           '-c', 'init.defaultBranch=main') + args, cwd=cwd, input=inp, check=True,
                          capture_output=True, text=True).stdout


def write(path, data=b'x'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)


def packs(repo):
    return sorted(n for n in os.listdir(os.path.join(repo, '.git', 'objects', 'pack')) if n.endswith('.pack'))


def by_check(rep):
    out = {}
    for c in rep['checks']:
        out.setdefault(c['check'], []).append(c)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.repo = os.path.realpath(os.path.join(self.t.name, 'repo'))
        os.makedirs(self.repo)
        sh(self.repo, 'init', '-q')
        write(os.path.join(self.repo, 'notes.md'), b'# research notes\n')
        sh(self.repo, 'add', 'notes.md')
        sh(self.repo, 'commit', '-qm', 'start')

    def doctor(self, repo=None, **kw):
        kw.setdefault('environ', {})
        kw.setdefault('platform', 'linux')
        return D.doctor(repo or self.repo, **kw)

    def add_packs(self, n):
        start = len(packs(self.repo)) if os.path.isdir(os.path.join(self.repo, '.git', 'objects', 'pack')) else 0
        for i in range(start, start + n):   # a new blob each time: the same blob would give the same pack name
            oid = sh(self.repo, 'hash-object', '-w', '--stdin', inp='invented blob %d\n' % i).strip()
            sh(self.repo, 'pack-objects', '-q', '.git/objects/pack/pack', inp=oid + '\n')


class Clean(Base):
    def test_clean_repo_has_no_findings(self):
        rep = self.doctor()
        self.assertEqual(rep['findings'], 0, rep)
        self.assertFalse(rep['partial_clone'])

    def test_main_exit_codes(self):
        a = argparse.Namespace(repo=self.repo, json=False, max_packs=D.PACKS, max_tmp_gb=D.TMP_GB,
                               max_binary_gb=D.BINARY_GB)
        lines = []
        with mock.patch.dict(os.environ, {'GIT_NO_LAZY_FETCH': '1'}):
            self.assertEqual(D.main(a, out=lines.append), 0)
            a.max_packs = -1   # any pack is too many
            self.assertEqual(D.main(a, out=lines.append), 1)
        self.assertTrue(any(l.startswith('warn  packs:') for l in lines), lines)
        self.assertTrue(any('git repack --geometric=2 -d' in l for l in lines), lines)

    def test_not_a_repo_exits_2(self):
        empty = os.path.join(self.t.name, 'plain')
        os.makedirs(empty)
        for path in (empty, os.path.join(self.t.name, 'missing')):
            out, err = [], io.StringIO()
            with redirect_stderr(err):
                rc = D.main(argparse.Namespace(repo=path, json=True, max_packs=1, max_tmp_gb=1, max_binary_gb=1),
                            out=out.append)
            self.assertEqual(rc, 2)
            self.assertIn('error', json.loads(out[0]))
            self.assertIn('gwt-rawstore doctor:', err.getvalue())

    def test_non_utf8_path_is_no_crash(self):
        """A tracked path that is not UTF-8 (an old Latin-1 archive) is read, not a traceback that exits 1."""
        oid = sh(self.repo, 'hash-object', '-w', '--stdin', inp='invented scan\n').strip()
        subprocess.run([b'git', b'update-index', b'--add', b'--cacheinfo', b'100644,' + oid.encode() + b',bad\xff.pdf'],
                       cwd=self.repo, check=True, capture_output=True)
        for as_json in (True, False):
            out = []
            with redirect_stderr(io.StringIO()) as err:
                rc = D.main(argparse.Namespace(repo=self.repo, json=as_json, max_packs=D.PACKS, max_tmp_gb=D.TMP_GB,
                                               max_binary_gb=D.BINARY_GB), out=out.append)
            self.assertEqual((rc, err.getvalue()), (0, ''), out)
            if as_json:
                self.assertEqual(json.loads(out[0])['findings'], 0)

    def test_unexpected_error_exits_2(self):
        a = argparse.Namespace(repo=self.repo, json=True, max_packs=D.PACKS, max_tmp_gb=D.TMP_GB,
                               max_binary_gb=D.BINARY_GB)
        out = []
        with mock.patch.object(D, 'tree_size', side_effect=ValueError('invented')), mock.patch.object(
                D, 'SCRATCH_DIRS', ('.',)), redirect_stderr(io.StringIO()):
            self.assertEqual(D.main(a, out=out.append), 2)
        self.assertIn('internal error', json.loads(out[0])['error'])

    def test_printed_lines_are_valid_utf8(self):
        """A non-UTF-8 name read from git is printed with U+FFFD, never as a lone surrogate that crashes print()."""
        report = {'repo': self.repo, 'git': '2.54.0', 'partial_clone': False, 'findings': 1,
                  'checks': [{'check': 'binaries', 'level': 'warn', 'message': 'arch\udcff/ 2.0 MB',
                              'fix': 'gwt-rawstore migrate arch\udcff --evict'}]}
        out = []
        with mock.patch.object(D, 'doctor', return_value=report):
            D.main(argparse.Namespace(repo=self.repo, json=False, max_packs=1, max_tmp_gb=1, max_binary_gb=1),
                   out=out.append)
        for line in out:
            line.encode('utf-8')
        self.assertTrue(any('arch\ufffd' in l for l in out), out)

    def test_partial_clone_read_from_config(self):
        """A partial clone is known from its config even before any promisor pack exists."""
        sh(self.repo, 'config', 'remote.origin.partialclonefilter', 'blob:none')
        rep = self.doctor()
        self.assertTrue(rep['partial_clone'])
        self.assertIn('blob:none', by_check(rep)['lazy-fetch'][0]['message'])

    def test_no_git_cannot_run(self):
        with mock.patch.object(D, 'run', side_effect=FileNotFoundError('git')):
            with self.assertRaises(D.CannotRun):
                self.doctor()

    def test_cli_subcommand_needs_no_store_config(self):
        with mock.patch.dict(os.environ, {'GWT_RAWSTORE_CONFIG': os.path.join(self.t.name, 'none')}), \
                redirect_stderr(io.StringIO()), mock.patch('builtins.print'):
            self.assertEqual(R.main(['doctor', '--repo', os.path.join(self.t.name, 'nowhere')]), 2)
            self.assertIn(R.main(['doctor', '--repo', self.repo, '--json']), (0, 1))


class Findings(Base):
    def test_too_many_packs(self):
        self.add_packs(4)
        n = len(packs(self.repo))
        self.assertEqual(by_check(self.doctor(max_packs=n))['packs'][0]['level'], 'ok')   # the limit itself is fine
        c = by_check(self.doctor(max_packs=n - 1))['packs'][0]
        self.assertEqual(c['level'], 'warn')
        self.assertIn('git multi-pack-index write', c['fix'])

    def test_missing_multi_pack_index(self):
        self.add_packs(4)
        with mock.patch.object(D, 'MIDX_PACKS', 3):
            c = by_check(self.doctor())['multi-pack-index'][0]
            self.assertEqual((c['level'], c['fix']), ('warn', 'git multi-pack-index write'))
            sh(self.repo, 'multi-pack-index', 'write')
            self.assertEqual(by_check(self.doctor())['multi-pack-index'][0]['level'], 'ok')

    def test_stale_multi_pack_index(self):
        """A midx written once covers only the packs of that day; the ones added later are a finding again."""
        self.add_packs(2)
        sh(self.repo, 'multi-pack-index', 'write')
        self.add_packs(4)
        with mock.patch.object(D, 'MIDX_PACKS', 3):
            c = by_check(self.doctor())['multi-pack-index'][0]
            self.assertEqual((c['level'], c['fix']), ('warn', 'git multi-pack-index write'))
            self.assertIn('4 of', c['message'])
            sh(self.repo, 'multi-pack-index', 'write')
            self.assertEqual(by_check(self.doctor())['multi-pack-index'][0]['level'], 'ok')

    def test_multi_pack_index_header_is_read(self):
        """A file that is not a midx covers nothing; an incremental chain's .midx files are counted."""
        self.add_packs(4)
        pack = os.path.join(self.repo, '.git', 'objects', 'pack')
        with mock.patch.object(D, 'MIDX_PACKS', 3):
            write(os.path.join(pack, 'multi-pack-index'), b'JUNK\x01\x01\x01\x00\x00\x00\x00\x63')
            self.assertEqual(by_check(self.doctor())['multi-pack-index'][0]['level'], 'warn')
            os.remove(os.path.join(pack, 'multi-pack-index'))
            sh(self.repo, 'multi-pack-index', 'write')
            os.makedirs(os.path.join(pack, 'multi-pack-index.d'))
            os.rename(os.path.join(pack, 'multi-pack-index'), os.path.join(pack, 'multi-pack-index.d', 'x.midx'))
            self.assertEqual(by_check(self.doctor())['multi-pack-index'][0]['level'], 'ok')

    def test_tracked_binaries(self):
        write(os.path.join(self.repo, 'raw', 'scans', 'page-001.jpg'), b'\xff' * 2000)
        write(os.path.join(self.repo, 'raw', 'notes.txt'), b't' * 5000)   # text: never counted
        sh(self.repo, 'add', 'raw')
        sh(self.repo, 'commit', '-qm', 'scans')
        self.assertEqual(by_check(self.doctor())['binaries'][0]['level'], 'ok')
        c = by_check(self.doctor(max_binary_gb=TINY))['binaries'][0]
        self.assertEqual(c['level'], 'warn')
        self.assertIn('1 tracked binaries', c['message'])
        self.assertIn('gwt-rawstore migrate raw --evict', c['fix'])
        self.assertFalse(by_check(self.doctor(max_binary_gb=0.0000021))['binaries'][0]['level'] == 'warn')

    def test_big_scratch_folder(self):
        write(os.path.join(self.repo, 'tmp', 'lane-a', 'dump.bin'), b'0' * 2000)
        self.assertEqual(by_check(self.doctor())['scratch'][0]['level'], 'ok')
        c = by_check(self.doctor(max_tmp_gb=TINY))['scratch'][0]
        self.assertEqual(c['level'], 'warn')
        self.assertIn('tmp/', c['message'])

    def test_scratch_symlink_is_not_followed(self):
        """tmp/ pointing outside the repo holds nothing of the repo's: no line, no walk."""
        outside = os.path.join(self.t.name, 'elsewhere')
        write(os.path.join(outside, 'dump.bin'), b'0' * 2000)
        os.symlink(outside, os.path.join(self.repo, 'tmp'))
        self.assertNotIn('scratch', by_check(self.doctor(max_tmp_gb=TINY)))

    def test_cache_inside_the_repo(self):
        self.assertEqual(by_check(self.doctor())['cache'][0]['level'], 'ok')
        write(os.path.join(self.repo, 'tools', '.cache', 'x'), b'c' * 2000)
        write(os.path.join(self.repo, 'fscache', 'x'), b'f' * 2000)
        env = {'GWT_FSCACHE_DIR': os.path.join(self.repo, 'fscache'), 'GWT_RAWSTORE_CACHE': '~/.cache/gwt-rawstore'}
        rep = self.doctor(environ=env)
        self.assertEqual(rep['findings'], 0, rep)   # under --max-tmp-gb: listed with its size, no finding
        self.assertEqual(sum(c['level'] == 'ok' for c in by_check(rep)['cache']), 2)
        msgs = [c['message'] for c in by_check(self.doctor(environ=env, max_tmp_gb=TINY))['cache']
                if c['level'] == 'warn']
        self.assertEqual(len(msgs), 2, msgs)
        self.assertTrue(any('$GWT_FSCACHE_DIR' in m for m in msgs))
        self.assertTrue(any('tools/.cache' in m for m in msgs))

    def test_committed_ledger_folder_is_no_cache(self):
        """docs/ledger.md tells users to commit GWT_LEDGER_DIR inside the repo; that is no finding."""
        write(os.path.join(self.repo, 'research', 'ledger.d', 'lane-7.tsv'), b'l' * 2000)
        rep = self.doctor(environ={'GWT_LEDGER_DIR': os.path.join(self.repo, 'research', 'ledger.d')}, max_tmp_gb=TINY)
        self.assertEqual([c['level'] for c in by_check(rep)['cache']], ['ok'])

    def test_relative_cache_variable_read_from_the_top_of_the_repo(self):
        """GWT_FSCACHE_DIR=work/cache, as agent-loop.md sets it, is found with --repo from another folder."""
        write(os.path.join(self.repo, 'work', 'cache', 'x'), b'w' * 2000)
        cwd = os.getcwd()
        os.chdir(self.t.name)
        self.addCleanup(os.chdir, cwd)
        c = [c for c in by_check(self.doctor(environ={'GWT_FSCACHE_DIR': 'work/cache'}, max_tmp_gb=TINY))['cache']
             if c['level'] == 'warn']
        self.assertEqual(len(c), 1, c)
        self.assertIn('work/cache', c[0]['message'])
        self.assertEqual([x['level'] for x in by_check(self.doctor(environ={'GWT_FSCACHE_DIR': 'no/such'},
                                                                   max_tmp_gb=TINY))['cache']], ['ok'])


class FixCommands(Base):
    def test_every_fix_is_a_command_you_can_paste(self):
        """The fix line is the command alone: it parses as shell, and a path with a space stays one argument."""
        write(os.path.join(self.repo, 'My Scans', 'a.pdf'), b'%PDF' + b'0' * 2000)
        sh(self.repo, 'add', '.')
        sh(self.repo, 'commit', '-qm', 'scans')
        write(os.path.join(self.repo, 'tmp', 'dump.bin'), b'0' * 2000)
        write(os.path.join(self.repo, '.cache', 'x'), b'0' * 2000)
        self.add_packs(3)
        with mock.patch.object(D, 'run', TimeMachine.fake('Included')[1]), mock.patch.object(D, 'MIDX_PACKS', 1):
            rep = self.doctor(platform='darwin', max_packs=1, max_tmp_gb=TINY, max_binary_gb=TINY)
        warned = {c['check'] for c in rep['checks'] if c['level'] == 'warn'}
        self.assertEqual(warned, {'packs', 'multi-pack-index', 'binaries', 'scratch', 'cache', 'time-machine'})
        for c in rep['checks']:
            if c['fix']:
                r = subprocess.run(['bash', '-n', '-c', c['fix']], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, (c, r.stderr))
                self.assertNotIn('(', c['fix'], c)
        fixes = {c['check']: c['fix'] for c in rep['checks']}
        self.assertEqual(fixes['packs'], 'git repack --geometric=2 -d && git multi-pack-index write')
        self.assertEqual(shlex.split(fixes['binaries']), ['gwt-rawstore', 'migrate', 'My Scans', '--evict'])
        tm = [shlex.split(x) for x in fixes['time-machine'].split(' && ')]
        self.assertIn(['tmutil', 'addexclusion', os.path.join(self.repo, 'tmp')], tm)
        self.assertIn(['tmutil', 'addexclusion', os.path.join(self.repo, 'My Scans')], tm)   # big binary folder


class TimeMachine(Base):
    @staticmethod
    def fake(answer):
        calls, real = [], D.run

        def run(args, cwd, timeout=120):
            if args[0] == 'tmutil':
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, '[%s]    %s\n' % (answer, args[-1]), '')
            return real(args, cwd, timeout)
        return calls, run

    def fake_tmutil(self, answer):
        calls, run = self.fake(answer)
        return calls, mock.patch.object(D, 'run', run)

    def test_linux_skips_with_a_note(self):
        calls, patch = self.fake_tmutil('Included')
        with patch:
            c = by_check(self.doctor(max_tmp_gb=TINY))['time-machine'][0]
        self.assertEqual(c['level'], 'skip')
        self.assertIn('not macOS', c['message'])
        self.assertEqual(calls, [])

    def test_included_big_folders_are_findings(self):
        write(os.path.join(self.repo, 'tmp', 'dump.bin'), b'0' * 2000)
        calls, patch = self.fake_tmutil('Included')
        with patch:
            c = by_check(self.doctor(platform='darwin', max_tmp_gb=TINY))['time-machine'][0]
        self.assertEqual(c['level'], 'warn')
        self.assertIn('tmutil addexclusion %s' % os.path.join(self.repo, 'tmp'), c['fix'])
        self.assertIn(['tmutil', 'isexcluded'], [list(a[:2]) for a in calls])

    def test_no_tmutil_is_a_skip(self):
        write(os.path.join(self.repo, 'tmp', 'dump.bin'), b'0' * 2000)
        real = D.run

        def run(args, cwd, timeout=120):
            if args[0] == 'tmutil':
                raise FileNotFoundError('tmutil')
            return real(args, cwd, timeout)
        with mock.patch.object(D, 'run', run):
            c = by_check(self.doctor(platform='darwin', max_tmp_gb=TINY))['time-machine'][0]
        self.assertEqual(c['level'], 'skip')

    def test_excluded_big_folders_are_fine(self):
        write(os.path.join(self.repo, 'tmp', 'dump.bin'), b'0' * 2000)
        calls, patch = self.fake_tmutil('Excluded')
        with patch:
            c = by_check(self.doctor(platform='darwin', max_tmp_gb=TINY))['time-machine'][0]
        self.assertEqual(c['level'], 'ok')
        self.assertTrue(calls)


class PartialClone(unittest.TestCase):
    """A blob:none clone over file://, with a folder outside the sparse checkout whose blobs are not local."""

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        root = os.path.realpath(self.t.name)
        self.src, self.repo = os.path.join(root, 'src'), os.path.join(root, 'clone')
        os.makedirs(self.src)
        sh(self.src, 'init', '-q')
        sh(self.src, 'config', 'uploadpack.allowFilter', 'true')
        sh(self.src, 'config', 'uploadpack.allowAnySHA1InWant', 'true')
        write(os.path.join(self.src, 'README.md'), b'invented\n')
        write(os.path.join(self.src, 'raw', 'page-001.png'), b'\x89PNG' + b'1' * 3000)
        write(os.path.join(self.src, 'raw', 'page-002.png'), b'\x89PNG' + b'2' * 3000)
        sh(self.src, 'add', '.')
        sh(self.src, 'commit', '-qm', 'invented scans')
        sh(root, 'clone', '-q', '--filter=blob:none', '--sparse', 'file://' + self.src, self.repo)

    def promisor_packs(self, n, age=0):
        """n invented promisor packs, as lazy fetches leave them, written `age` seconds ago."""
        pack = os.path.join(self.repo, '.git', 'objects', 'pack')
        for i in range(n):
            oid = sh(self.repo, 'hash-object', '-w', '--stdin', inp='lazy blob %d\n' % len(packs(self.repo))).strip()
            name = sh(self.repo, 'pack-objects', '-q', os.path.join(pack, 'pack'), inp=oid + '\n').strip()
            write(os.path.join(pack, 'pack-%s.promisor' % name), b'')
            t = time.time() - age
            os.utime(os.path.join(pack, 'pack-%s.pack' % name), (t, t))

    def test_lazy_fetch_is_measured_from_recent_promisor_packs(self):
        """The finding is packs piling up today, whoever runs doctor; this shell's variable is only information."""
        rep = D.doctor(self.repo, environ={}, platform='linux')
        self.assertTrue(rep['partial_clone'])
        c = by_check(rep)['lazy-fetch'][0]
        self.assertEqual(c['level'], 'info')   # a quiet partial clone run from a plain shell is no finding
        self.assertIn('blob:none', c['message'])
        self.assertEqual(rep['findings'], 0)
        base = len(packs(self.repo))   # the clone's own promisor packs, written just now
        self.promisor_packs(3, age=2 * 86400)   # old ones never count
        with mock.patch.object(D, 'PROMISOR_PER_DAY', base + 2):
            self.assertEqual(by_check(D.doctor(self.repo, environ={}, platform='linux'))['lazy-fetch'][0]['level'],
                             'info')
            self.promisor_packs(2)   # the limit itself is still fine
            self.assertEqual(by_check(D.doctor(self.repo, environ={}, platform='linux'))['lazy-fetch'][0]['level'],
                             'info')
            self.promisor_packs(1)
            for env in ({}, {'GIT_NO_LAZY_FETCH': '1'}):   # the variable in this shell does not hide it
                c = by_check(D.doctor(self.repo, environ=env, platform='linux'))['lazy-fetch'][0]
                self.assertEqual((c['level'], c['fix']), ('warn', 'export GIT_NO_LAZY_FETCH=1'))
                self.assertIn('%d promisor packs' % (base + 3), c['message'])

    def test_plain_packs_are_not_lazy_fetches(self):
        base = len(packs(self.repo))
        for i in range(3):   # recent packs with no .promisor marker: a repack or a push, not a lazy fetch
            oid = sh(self.repo, 'hash-object', '-w', '--stdin', inp='plain blob %d\n' % i).strip()
            sh(self.repo, 'pack-objects', '-q', '.git/objects/pack/pack', inp=oid + '\n')
        with mock.patch.object(D, 'PROMISOR_PER_DAY', base):
            self.assertEqual(by_check(D.doctor(self.repo, environ={}, platform='linux'))['lazy-fetch'][0]['level'],
                             'info')

    def test_old_git_is_told_to_upgrade_before_repacking(self):
        for minimum, said in (((99, 0), True), ((0, 0), False)):
            with mock.patch.object(D, 'GEOMETRIC_PROMISOR', minimum):
                c = by_check(D.doctor(self.repo, max_packs=0, environ={}, platform='linux'))['packs'][0]
            self.assertEqual(c['level'], 'warn')
            self.assertEqual('Upgrade git to 2.53' in c['message'], said, c)

    def test_lazy_fetch_variable_parsed_like_git(self):
        for v, level in (('1', 'ok'), ('true', 'ok'), ('2', 'ok'), ('0', 'info'), ('false', 'info'), ('', 'info')):
            c = by_check(D.doctor(self.repo, environ={'GIT_NO_LAZY_FETCH': v}, platform='linux'))['lazy-fetch'][0]
            self.assertEqual(c['level'], level, v)

    def test_doctor_never_fetches(self):
        """Every process doctor starts is a known read-only command with lazy fetch off, and no pack appears;
        the control read at the end shows that a lazy fetch in this clone does add a pack."""
        before, calls, real = packs(self.repo), [], subprocess.run

        def record(args, **kw):
            calls.append((tuple(args), kw.get('env') or {}))
            if args[0] == 'tmutil':
                return subprocess.CompletedProcess(args, 0, '[Included]    %s\n' % args[-1], '')
            return real(args, **kw)
        with mock.patch.object(D.subprocess, 'run', record):
            rep = D.doctor(self.repo, max_packs=0, max_tmp_gb=0, max_binary_gb=0, environ={}, platform='darwin')
        self.assertGreater(rep['findings'], 0)
        self.assertTrue(calls)
        for args, env in calls:
            self.assertEqual((env.get('GIT_NO_LAZY_FETCH'), env.get('GIT_TERMINAL_PROMPT'), env.get('GIT_OPTIONAL_LOCKS')),
                             ('1', '0', '0'), args)
            if args[0] == 'git':
                self.assertIn(args[1], ('version', 'rev-parse', 'count-objects', 'config', 'ls-files'), args)
            else:
                self.assertEqual(args[:2], ('tmutil', 'isexcluded'))
        self.assertEqual(packs(self.repo), before)
        # control: reading a blob outside the sparse checkout fetches it as a new pack
        sh(self.repo, 'cat-file', '-s', 'HEAD:raw/page-001.png')
        self.assertNotEqual(packs(self.repo), before)

    def test_binaries_outside_the_sparse_checkout_are_not_read(self):
        c = by_check(D.doctor(self.repo, environ={}, platform='linux', max_binary_gb=0))['binaries'][0]
        self.assertEqual((c['level'], c['message']), ('ok', '0 tracked binaries, 0.0 MB'))


if __name__ == '__main__':
    unittest.main()
