"""A backup-only identity must never become the change-list writer."""
import json
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
        return self.fake.request(method, url, **kw)

    def close(self):
        self.closed = True


def setup(tmp_path, monkeypatch, *, config_extra='', mode=0o600, tree='tree-test', login_status=200):
    import gwt_apply.client as clientmod
    fake = FakeGrampsWeb()
    fake.add('people', {'handle': 'person', 'gender': 0, 'primary_name': {'first_name': 'Example'}})
    calls = []
    writer = Session(fake, 'writer', calls)
    backup = Session(fake, 'backup', calls, login_tree=tree, login_status=login_status)
    sessions = iter([writer, backup])
    monkeypatch.setattr(clientmod.requests, 'Session', lambda: next(sessions))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    for key, value in {'GWT_URL': 'https://gw.example', 'GWT_USERNAME': 'writer',
                       'GWT_PASSWORD': 'writer-secret', 'GWT_TREE': 'tree-test'}.items():
        monkeypatch.setenv(key, value)
    changes = tmp_path / 'changes.jsonl'
    changes.write_text(json.dumps({'op': 'add_note', 'key': 'n', 'ruling': 'ruling-example',
                                  'text': 'Example evidence', 'attach': [{'person': 'I0001'}]}) + '\n')
    config = tmp_path / 'backup.env'
    config.write_text('GWT_USERNAME=backup\nGWT_PASSWORD=backup-secret\n' + config_extra)
    config.chmod(mode)
    args = [str(changes), '--apply', '--server-backup', '--backup-config', str(config),
            '--log', str(tmp_path / 'run.jsonl')]
    return fake, calls, backup, args, config


def test_two_identities_keep_tree_writes_on_writer(tmp_path, monkeypatch):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    assert main(args) == 0
    assert backup.closed
    assert ('backup', 'POST', '/api/backups/') in calls
    assert all(identity == 'writer' for identity, method, path in calls if path == '/api/objects/')
    logs = '\n'.join(p.read_text() for p in tmp_path.glob('run.*.jsonl'))
    assert 'server-backup' in logs and 'bk1' in logs and 'backup_login' in logs
    assert 'writer-secret' not in logs and 'backup-secret' not in logs


@pytest.mark.parametrize('extra,mode,tree,status', [
    ('GWT_URL=https://other.example\n', 0o600, 'tree-test', 200),
    ('GWT_TREE=other-tree\n', 0o600, 'tree-test', 200),
    ('', 0o644, 'tree-test', 200),
    ('', 0o600, 'other-tree', 200),
    ('', 0o600, 'tree-test', 403),
])
def test_bad_config_or_login_never_applies(tmp_path, monkeypatch, extra, mode, tree, status):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch, config_extra=extra, mode=mode,
                                             tree=tree, login_status=status)
    assert main(args) == 1
    assert not fake.store['notes']  # temporary writer lock released, no change-list note
    assert ('backup', 'POST', '/api/backups/') not in calls


def test_missing_credentials_do_not_inherit_writer_environment(tmp_path, monkeypatch):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    config.write_text('GWT_USERNAME=backup\n')
    assert main(args) == 1
    assert not fake.store['notes']
    assert not any(identity == 'backup' for identity, method, path in calls)


def test_separate_backup_refusal_stops_tree_changes(tmp_path, monkeypatch):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    fake.backup_status = 403
    assert main(args) == 1
    assert not fake.store['notes'] and backup.closed


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


def test_backup_config_without_gate_is_refused():
    with pytest.raises(SystemExit):
        main(['example.jsonl', '--apply', '--backup-config', 'backup.env'])


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


def test_backup_symlink_is_refused(tmp_path, monkeypatch):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    link = tmp_path / 'linked.env'
    link.symlink_to(config)
    args[args.index('--backup-config') + 1] = str(link)
    assert main(args) == 1
    assert not fake.store['notes']


def test_backup_task_failure_is_fail_closed(tmp_path, monkeypatch):
    fake, calls, backup, args, config = setup(tmp_path, monkeypatch)
    fake.backup_status = 202
    fake.task_script = [{'state': 'FAILURE'}]
    assert main(args) == 1
    assert not fake.store['notes'] and backup.closed
