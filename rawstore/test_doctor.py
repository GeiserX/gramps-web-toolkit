"""gwt-rawstore doctor against small repos built in a temp folder. Every repo, file and size here is invented."""
import argparse, io, json, os, subprocess, sys, tempfile, unittest
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
        for i in range(n):
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

    def test_tracked_binaries(self):
        write(os.path.join(self.repo, 'raw', 'scans', 'page-001.jpg'), b'\xff' * 2000)
        write(os.path.join(self.repo, 'raw', 'notes.txt'), b't' * 5000)   # text: never counted
        sh(self.repo, 'add', 'raw')
        sh(self.repo, 'commit', '-qm', 'scans')
        self.assertEqual(by_check(self.doctor())['binaries'][0]['level'], 'ok')
        c = by_check(self.doctor(max_binary_gb=TINY))['binaries'][0]
        self.assertEqual(c['level'], 'warn')
        self.assertIn('1 tracked binaries', c['message'])
        self.assertIn('gwt-rawstore migrate raw --remove', c['fix'])
        self.assertFalse(by_check(self.doctor(max_binary_gb=0.0000021))['binaries'][0]['level'] == 'warn')

    def test_big_scratch_folder(self):
        write(os.path.join(self.repo, 'tmp', 'lane-a', 'dump.bin'), b'0' * 2000)
        self.assertEqual(by_check(self.doctor())['scratch'][0]['level'], 'ok')
        c = by_check(self.doctor(max_tmp_gb=TINY))['scratch'][0]
        self.assertEqual(c['level'], 'warn')
        self.assertIn('tmp/', c['message'])

    def test_cache_inside_the_repo(self):
        self.assertEqual(by_check(self.doctor())['cache'][0]['level'], 'ok')
        write(os.path.join(self.repo, 'tools', '.cache', 'x'))
        rep = self.doctor(environ={'GWT_FSCACHE_DIR': os.path.join(self.repo, 'fscache'),
                                   'GWT_RAWSTORE_CACHE': '~/.cache/gwt-rawstore'})
        msgs = [c['message'] for c in by_check(rep)['cache'] if c['level'] == 'warn']
        self.assertEqual(len(msgs), 2, msgs)
        self.assertTrue(any('$GWT_FSCACHE_DIR' in m for m in msgs))
        self.assertTrue(any('tools/.cache' in m for m in msgs))


class TimeMachine(Base):
    def fake_tmutil(self, answer):
        calls, real = [], D.run

        def run(args, cwd, timeout=120):
            if args[0] == 'tmutil':
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, '[%s]    %s\n' % (answer, args[-1]), '')
            return real(args, cwd, timeout)
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

    def test_lazy_fetch_on_is_a_finding_and_off_is_not(self):
        rep = D.doctor(self.repo, environ={}, platform='linux')
        self.assertTrue(rep['partial_clone'])
        c = by_check(rep)['lazy-fetch'][0]
        self.assertEqual(c['level'], 'warn')
        self.assertIn('blob:none', c['message'])
        self.assertIn('export GIT_NO_LAZY_FETCH=1', c['fix'])
        self.assertEqual(by_check(D.doctor(self.repo, environ={'GIT_NO_LAZY_FETCH': 'true'}, platform='linux'))
                         ['lazy-fetch'][0]['level'], 'ok')

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
            self.assertEqual(env.get('GIT_NO_LAZY_FETCH'), '1', args)
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
