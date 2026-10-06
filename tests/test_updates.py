import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import core
import server
import update

def manifests(current='0.70.1', exe=None, daemon=None):
    digest = hashlib.sha256(b'installer').hexdigest()
    return {
        'windows-amd64.json': dict(Version=current, Installer='SideloadlySetup64.exe', Sha256=digest),
        'exe-windows-amd64.json': dict(Version=current, Sha256=exe or digest),
        'daemon-windows-amd64.json': dict(Version=current, Sha256=daemon or digest),
    }

class Updates(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.patches = [patch.object(module, 'ROOT', self.root) for module in (core, update, server)]
        self.patches += [patch.object(module, 'STATE', self.root / 'data' / 'active.json') for module in (core, update)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def source(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'sideloadly.exe').write_bytes(b'head' + core.OLD + b'tail')
        (source / 'sideloadlydaemon.exe').write_bytes(b'daemon')
        for name in ('sideloadly.dll', 'python38.dll', 'python38.zip', '_ssl.pyd', 'an/iTunesCore.dll'):
            path = source / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'dependency')
        return source

    def active(self):
        source = self.source()
        data = manifests(exe=core.sha256(source / 'sideloadly.exe'), daemon=core.sha256(source / 'sideloadlydaemon.exe'))
        state = update.stage_update(source, data)
        core.atomic_json(core.STATE, state)
        return state, data

    def test_numeric_version_and_rejection(self):
        self.assertGreater(core.version('0.100.0'), core.version('0.99.9'))
        self.assertEqual(core.version('0.70'), core.version('0.70.0'))
        for value in ('../1', 'v1.2', '1.2-beta', None):
            with self.assertRaises(ValueError):
                core.version(value)

    def test_hash_formats_and_invalid_hash(self):
        raw = hashlib.sha256(b'test').digest()
        self.assertEqual(core.expected_hash(base64.b64encode(raw).decode()), raw.hex())
        self.assertEqual(core.expected_hash(raw.hex().upper()), raw.hex())
        with self.assertRaises(ValueError):
            core.expected_hash('bad')

    def test_mixed_manifest_versions_and_unsafe_filename(self):
        data = manifests()
        data['exe-windows-amd64.json']['Version'] = '0.71.0'
        with self.assertRaises(ValueError):
            core.validate_manifests(data)
        data = manifests()
        data['windows-amd64.json']['Installer'] = '../file.exe'
        with self.assertRaises(ValueError):
            core.validate_manifests(data)

    def test_patch_exact_and_original_unchanged(self):
        source = self.source()
        original = (source / 'sideloadly.exe').read_bytes()
        state = update.stage_update(source, manifests(exe=core.sha256(source / 'sideloadly.exe'),
                                                    daemon=core.sha256(source / 'sideloadlydaemon.exe')))
        patched = (core.local_path(state['app']) / 'sideloadly.exe').read_bytes()
        self.assertEqual(patched, original.replace(core.OLD, core.NEW))
        self.assertEqual(len(patched), len(original))
        self.assertEqual((source / 'sideloadly.exe').read_bytes(), original)
        self.assertFalse(core.STATE.exists())

    def test_incompatible_prefix_cannot_activate(self):
        state, data = self.active()
        source = self.root / 'source'
        (source / 'sideloadly.exe').write_bytes(b'new incompatible binary')
        data['exe-windows-amd64.json']['Sha256'] = core.sha256(source / 'sideloadly.exe')
        with self.assertRaises(ValueError):
            update.stage_update(source, data)
        self.assertEqual(core.read_state(), state)

    def test_bad_original_hash_cannot_stage(self):
        source = self.source()
        with self.assertRaises(ValueError):
            update.stage_update(source, manifests())
        self.assertFalse(core.STATE.exists())

    def test_promotion_and_rollback_preserve_snapshot(self):
        active, data = self.active()
        newer = copy.deepcopy(data)
        for item in newer.values():
            item['Version'] = '0.71.0'
        candidate = update.stage_update(self.root / 'source', newer)
        update.promote(candidate, active)
        self.assertEqual(core.read_state()['version'], '0.71.0')
        previous = json.loads((self.root / 'data' / 'previous.json').read_text())
        update.promote(previous, core.read_state())
        self.assertEqual(core.read_state(), active)

    def test_corrupt_candidate_cannot_promote(self):
        active, data = self.active()
        candidate = update.stage_update(self.root / 'source', data)
        (core.local_path(candidate['app']) / 'sideloadly.exe').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            update.promote(candidate, active)
        self.assertEqual(core.read_state(), active)

    def test_offline_check_leaves_active_unchanged(self):
        state, data = self.active()
        with patch.object(update, 'fetch_manifests', side_effect=URLError('offline')):
            with self.assertRaises(URLError):
                update.run(update.settings(), force=True)
        self.assertEqual(core.read_state(), state)

    def test_same_version_different_build_rejected(self):
        state, data = self.active()
        data['exe-windows-amd64.json']['Sha256'] = '00' * 32
        with patch.object(update, 'fetch_manifests', return_value=data):
            with self.assertRaises(ValueError):
                update.run(update.settings(), force=True)
        self.assertEqual(core.read_state(), state)

    def test_download_hash_mismatch_preserves_cached_file(self):
        destination = self.root / 'installer.exe'
        destination.write_bytes(b'existing')
        response = io.BytesIO(b'corrupt')
        response.url = update.OFFICIAL + 'SideloadlySetup64.exe'
        client = unittest.mock.Mock()
        client.open.return_value = response
        with self.assertRaises(ValueError):
            update.download(client, response.url, destination, '00' * 32, 1)
        self.assertEqual(destination.read_bytes(), b'existing')
        self.assertFalse(list(self.root.glob('*.part')))

    def test_loopback_server_pins_snapshot_and_rejects_traversal(self):
        folder = self.root / 'manifests'
        data = manifests()
        core.save_manifests(folder, data)
        service = server.build_server(folder, port=0)
        worker = threading.Thread(target=service.serve_forever, daemon=True)
        worker.start()
        origin = f'http://127.0.0.1:{service.server_port}'
        try:
            self.assertEqual(service.server_address[0], '127.0.0.1')
            changed = manifests('0.71.0')
            core.save_manifests(folder, changed)
            for endpoint in ('windows-amd64.json', 'exe/windows-amd64.json', 'daemon/windows-amd64.json'):
                with urlopen(origin + '/rrrrrr/' + endpoint) as response:
                    self.assertEqual(json.load(response)['Version'], '0.70.1')
            for endpoint in ('/../../config.json', '/rrrrrr/installer.exe'):
                with self.assertRaises(HTTPError) as error:
                    urlopen(origin + endpoint)
                self.assertEqual(error.exception.code, 404)
        finally:
            service.shutdown()
            service.server_close()
            worker.join()

    def test_state_path_cannot_escape(self):
        with self.assertRaises(ValueError):
            core.local_path('../outside.exe')

    def test_downloaded_update_without_extractor_keeps_old_version(self):
        state, data = self.active()
        for item in data.values():
            item['Version'] = '0.71.0'
        def fake_download(client, url, target, expected, timeout):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'installer')
            self.assertEqual(core.sha256(target), expected)
        with patch.object(update, 'fetch_manifests', return_value=data), \
             patch.object(update, 'download', side_effect=fake_download), \
             patch.object(update, 'extractor_path', return_value=None):
            message = update.run(update.settings(), force=True)
        self.assertIn('downloaded and verified', message)
        self.assertEqual(core.read_state(), state)
        status = json.loads((self.root / 'data' / 'update-status.json').read_text())
        self.assertEqual(status['result'], 'downloaded')
        self.assertTrue(Path(status['installer']).is_file())

    def test_update_lock_blocks_second_updater_and_recovers(self):
        with update.update_lock():
            with self.assertRaises(OSError):
                with update.update_lock():
                    self.fail('Concurrent update acquired lock')
        with update.update_lock():
            pass

    def test_check_only_new_version_does_not_throttle_download(self):
        state, data = self.active()
        for item in data.values():
            item['Version'] = '0.71.0'
        with patch.object(update, 'fetch_manifests', return_value=data), \
             patch.object(update, 'download') as downloader:
            update.run(update.settings(), force=True, check_only=True)
        downloader.assert_not_called()
        self.assertFalse((self.root / 'data' / 'update-status.json').exists())
        self.assertEqual(core.read_state(), state)

if __name__ == '__main__':
    unittest.main()
