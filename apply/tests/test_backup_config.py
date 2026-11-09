"""The GWT_BACKUP_* login makes only the server backup; the writer keeps the lock and every tree write."""
import json
import os
from urllib.parse import urlparse

import pytest
from fake_api import FakeGrampsWeb, Resp, make_jwt
from gwt_apply.cli import main, _BackupClient
from gwt_apply.client import ApiError


class Session:
    def __init__(self, fake, identity, calls, *, login_tree='tree-test', login_status=200):
        self.fake, self.identity, self.calls = fake, identity, calls
        self.login_tree, self.login_status = login_tree, login_status
        self.closed = False

    def request(self, method, url, **kw):
        path = urlparse(url).path
        self.calls.append((self.identity, method, path))
        if path == '/api/token/':
            expected = {'username': self.identity, 'password': self.identity + '-secret'}
            assert kw['json'] == expected  # writer environment must not replace backup config
            return Resp(self.login_status, {'access_token': make_jwt(self.login_tree)})
        if self.identity == 'backup':
            assert path == '/api/backups/' or path.startswith('/api/tasks/'), path
        else:
            assert path != '/api/backups/' and not path.startswith('/api/tasks/'), path
        if self.identity == 'backup':
            assert kw.pop("allow_redirects") is False
        return self.fake.request(method, url, **kw)

    def close(self):
        self.closed = True


def setup(tmp_path, monkeypatch, *, config_extra='', tree='tree-test', login_status=200, env=False):
    import gwt_apply.client as clientmod
    fake = FakeGrampsWeb()
    fake.add('people', {'handle': 'person', 'gender': 0, 'primary_name': {'first_name': 'Example'}})
    calls = []
    writer = Session(fake, 'writer', calls)
    backup = Session(fake, 'backup', calls, login_tree=tree, login_status=login_status)
    sessions = iter([writer, backup])
    monkeypatch.setattr(clientmod.requests, 'Session', lambda: next(sessions))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    for key in [k for k in os.environ if k.startswith('GWT_')]:
        monkeypatch.delenv(key)
    for key, value in {'GWT_URL': 'https://gw.example', 'GWT_USERNAME': 'writer',
                       'GWT_PASSWORD': 'writer-secret', 'GWT_TREE': 'tree-test'}.items():
        monkeypatch.setenv(key, value)
    changes = tmp_path / 'changes.jsonl'
    changes.write_text(json.dumps({'op': 'add_note', 'key': 'n', 'ruling': 'ruling-example',
                                  'text': 'Example evidence', 'attach': [{'person': 'I0001'}]}) + '\n')
    config = tmp_path / 'gw.env'
    if env:
        monkeypatch.setenv('GWT_BACKUP_USERNAME', 'backup')
        monkeypatch.setenv('GWT_BACKUP_PASSWORD', 'backup-secret')
        config.write_text(config_extra)
    else:
        config.write_text('GWT_BACKUP_USERNAME=backup\nGWT_BACKUP_PASSWORD=backup-secret\n' + config_extra)
    args = [str(changes), '--apply', '--server-backup', '--config', str(config),
            '--log', str(tmp_path / 'run.jsonl')]
    return fake, calls, backup, args, config


@pytest.mark.parametrize('env', [False, True])
def test_two_identities_keep_tree_writes_on_writer(tmp_path, monkeypatch, env):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch, env=env)
    assert main(args) == 0
    assert backup.closed
    assert ('backup', 'POST', '/api/backups/') in calls
    assert all(identity == 'writer' for identity, method, path in calls if path == '/api/objects/')
    logs = '\n'.join(p.read_text() for p in tmp_path.glob('run.*.jsonl'))
    assert 'server-backup' in logs and 'bk1' in logs and 'backup_login' in logs
    assert 'writer-secret' not in logs and 'backup-secret' not in logs


def test_backup_keys_without_server_backup_are_ignored(tmp_path, monkeypatch):
    """gwt-run hands the same --config to every step: the keys must not change a run without --server-backup."""
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    args.remove('--server-backup')
    assert main(args) == 0
    assert calls and all(identity == 'writer' for identity, _, _ in calls)
    assert not any(path == '/api/backups/' for _, _, path in calls)


def test_half_backup_login_is_ignored_without_server_backup(tmp_path, monkeypatch):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    config.write_text('GWT_BACKUP_USERNAME=backup\n')
    args.remove('--server-backup')
    assert main(args) == 0


@pytest.mark.parametrize('tree,status,says', [
    ('other-tree', 200, 'the backup login failed: login landed in tree'),
    ('tree-test', 403, 'the backup login failed: login failed: HTTP 403'),
])
def test_bad_backup_login_never_applies(tmp_path, monkeypatch, capsys, tree, status, says):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch, tree=tree, login_status=status)
    assert main(args) == 1
    assert not fake.store['notes']  # temporary writer lock released, no change-list note
    assert ('backup', 'POST', '/api/backups/') not in calls
    assert says in capsys.readouterr().err


@pytest.mark.parametrize('content,says', [
    ('GWT_BACKUP_USERNAME=backup\n', 'missing setting: GWT_BACKUP_PASSWORD'),
    ('GWT_BACKUP_PASSWORD=backup-secret\n', 'missing setting: GWT_BACKUP_USERNAME'),
    ('GWT_BACKUP_TOKEN=example-sync\n', 'cannot create backups'),
    ('GWT_BACKUP_USERNAME=backup\nGWT_BACKUP_PASSWORD=backup-secret\nGWT_BACKUP_TOKEN=example-sync\n',
     'cannot create backups'),
])
def test_incomplete_backup_login_stops_before_the_server(tmp_path, monkeypatch, capsys, content, says):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    config.write_text(content)
    assert main(args) == 2
    assert not calls  # no login, no lock
    err = capsys.readouterr().err
    assert says in err and 'backup-secret' not in err and 'example-sync' not in err


def test_separate_backup_refusal_stops_tree_changes(tmp_path, monkeypatch, capsys):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    fake.backup_status = 403
    assert main(args) == 1
    assert not fake.store['notes'] and backup.closed
    assert 'needs an owner or admin login' in capsys.readouterr().err


def test_separate_backup_task_and_list_verified(tmp_path, monkeypatch):
    import gwt_apply.cli as cli
    monkeypatch.setattr(cli.time, 'sleep', lambda _: None)
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    fake.backup_status = 202
    fake.task_script = [{'state': 'SUCCESS', 'result_object': {'id': 'example-backup'}}]
    fake.backup_items = [{'id': 'example-backup', 'size': 10, 'restorable': True}]
    assert main(args) == 0
    assert ('backup', 'GET', '/api/tasks/t1') in calls
    assert ('backup', 'GET', '/api/backups/') in calls
    assert sum(path == '/api/backups/' and method == 'POST' for _, method, path in calls) == 1


@pytest.mark.parametrize('items', [[], [{'id': 'example-backup', 'size': 10, 'restorable': False}]])
def test_separate_backup_bad_list_stops_changes(tmp_path, monkeypatch, items):
    import gwt_apply.cli as cli
    monkeypatch.setattr(cli.time, 'sleep', lambda _: None)
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    fake.backup_status = 202
    fake.task_script = [{'state': 'SUCCESS', 'result_object': {'id': 'example-backup'}}]
    fake.backup_items = items
    assert main(args) == 1
    assert not fake.store['notes']


@pytest.mark.parametrize('method,path', [('POST', '/objects/'), ('GET', '/people/'),
                                        ('DELETE', '/backups/example'), ('POST', '/tasks/example')])
def test_backup_client_rejects_unrelated_routes(method, path):
    client = _BackupClient('https://gw.example', 'backup', 'example', tree='tree-test')
    with pytest.raises(ApiError, match='only create/list backups'):
        client._request(method, path)
    client.session.close()


def test_backup_login_returned_tree_is_checked_independently(tmp_path, monkeypatch):
    import gwt_apply.cli as cli
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    original = cli._BackupClient.login
    def wrong_tree(self):
        original(self)
        self.tree = 'unexpected-tree'
    monkeypatch.setattr(cli._BackupClient, 'login', wrong_tree)
    assert main(args) == 1
    assert not fake.store['notes']
    assert ('backup', 'POST', '/api/backups/') not in calls


def test_backup_task_failure_is_fail_closed(tmp_path, monkeypatch):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    fake.backup_status = 202
    fake.task_script = [{'state': 'FAILURE'}]
    assert main(args) == 1
    assert not fake.store['notes'] and backup.closed


@pytest.mark.parametrize('code', [301, 302, 307, 308])
@pytest.mark.parametrize('route', ['login', 'sync', 'backup', 'task', 'list'])
def test_privileged_requests_never_follow_redirects(code, route):
    class RedirectSession:
        def __init__(self):
            self.calls = []
        def request(self, method, url, **kw):
            self.calls.append((method, url, kw))
            assert kw.get('allow_redirects') is False
            return Resp(code, {}, {'Location': '/api/objects/'})
        def close(self):
            pass
    session = RedirectSession()
    client = _BackupClient('https://gw.example', 'backup', 'example', tree='tree-test',
                           token='example-sync' if route == 'sync' else None, session=session)
    with pytest.raises(ApiError, match='refused an HTTP redirect'):
        if route in ('login', 'sync'):
            client.login()
        else:
            client.token = 'example-jwt'
            paths = {'backup': ('POST', '/backups/'), 'task': ('GET', '/tasks/example'),
                     'list': ('GET', '/backups/')}
            client._request(*paths[route])
    assert len(session.calls) == 1
    assert not any('/objects/' in url for _, url, _ in session.calls)
    client.session.close()


@pytest.mark.parametrize('code', [301, 302, 307, 308])
def test_backup_redirect_stops_applier(tmp_path, monkeypatch, capsys, code):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    original = backup.request
    def redirect(method, url, **kw):
        assert kw.get('allow_redirects') is False
        if urlparse(url).path == '/api/backups/':
            return Resp(code, {}, {'Location': '/api/objects/'})
        return original(method, url, **kw)
    backup.request = redirect
    assert main(args) == 1
    assert not fake.store['notes'] and backup.closed
    assert 'refused an HTTP redirect' in capsys.readouterr().err
