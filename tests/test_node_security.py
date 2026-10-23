"""Node2Shell regression checks. No downloads, installs, network or exploit inputs."""
import importlib
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# pwd is Linux-only, but the installer policy can also be tested on a Windows PC.
if os.name == 'nt':
    with patch.dict(sys.modules, {'pwd': SimpleNamespace()}):
        guest = importlib.import_module('backend.guest_software')
else:
    from backend import guest_software as guest
from backend.software import BY_ID, software_files, validate_software


def madison(*versions):
    return '\n'.join(f'nodejs | {v} | https://deb.nodesource.com test/main amd64 Packages' for v in versions)


class NodeSecurity(unittest.TestCase):
    def test_catalog_and_guest_policy_match(self):
        spec = BY_ID['node']
        self.assertEqual(spec['versions'], list(guest.NODE_INSTALL_MAJORS))
        for major, minimum in spec['minimum_versions'].items():
            self.assertEqual(tuple(map(int, minimum.split('.'))), guest.NODE_SECURITY_MINIMUM[major])
            self.assertIn(minimum, spec['note'])
            # Existing major-only metadata remains valid, without claiming it was patched.
            self.assertEqual(validate_software([dict(id='node', version=major)]), [dict(id='node', version=major)])

    def test_versions_fail_closed(self):
        for actual in ('v24.22.0', 'v22.23.3', 'v26.12.0', 'v20.20.2',
                       'v24.22.1-rc.1', '24', 'garbage', 'v24.22.1;echo unsafe'):
            with self.subTest(actual=actual), self.assertRaises(RuntimeError):
                guest.require_safe_node(actual)
        for actual in ('v24.22.1', 'v22.23.4', 'v26.12.1', 'v24.22.2', 'v24.23.0'):
            guest.require_safe_node(actual)
        with self.assertRaises(RuntimeError):
            guest.require_safe_node('v22.23.4', '24')

    def test_latest_safe_stable_package_selected_numerically(self):
        packages = madison('24.22.0-1nodesource1', '24.22.1-1nodesource1',
                           '22.23.4-1nodesource1', '24.23.0-1nodesource1',
                           '24.100.0-1nodesource1', '24.999.0-rc.1',
                           '24.999.0-1nodesource1;evil')
        self.assertEqual(guest.secure_node_package(packages, '24'), '24.100.0-1nodesource1')
        self.assertEqual(guest.secure_node_package(packages, '22'), '22.23.4-1nodesource1')
        for major, available in [('24', ''), ('24', madison('24.22.0-1nodesource1')),
                                 ('22', madison('24.22.1-1nodesource1')), ('20', packages),
                                 ('24;evil', packages), ('26', madison('26.12.1-1nodesource1'))]:
            with self.subTest(major=major), self.assertRaises(RuntimeError):
                guest.secure_node_package(available, major)

    def test_install_exact_safe_candidate_and_verify_executed_node(self):
        for major, minimum in BY_ID['node']['minimum_versions'].items():
            with self.subTest(major=major), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                def remap(value):
                    path = root / str(value).lstrip('/')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    return path
                def command(*args, **kwargs):
                    return {('apt-cache', 'madison', 'nodejs'): madison(minimum + '-1nodesource1'),
                            ('node', '--version'): 'v' + minimum, ('npm', '--version'): '11.20.0'}[args]
                with patch.object(guest, 'Path', remap), patch.object(guest, 'download'), \
                        patch.object(guest, 'update'), patch.object(guest, 'apt') as apt, \
                        patch.object(guest, 'run', side_effect=command):
                    self.assertIn('v' + minimum, guest.native('node', major))
                    apt.assert_called_once_with('nodejs=' + minimum + '-1nodesource1', 'build-essential')

    def test_repository_lag_and_wrong_executable_are_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def remap(value):
                path = root / str(value).lstrip('/')
                path.parent.mkdir(parents=True, exist_ok=True)
                return path
            for available, installed in [(madison('24.22.0-1nodesource1'), 'v24.22.0'),
                                         (madison('24.22.1-1nodesource1'), 'v24.22.0'),
                                         (madison('24.22.1-1nodesource1'), 'v22.23.4')]:
                def command(*args, **kwargs):
                    return available if args[0] == 'apt-cache' else installed
                with patch.object(guest, 'Path', remap), patch.object(guest, 'download'), \
                        patch.object(guest, 'update'), patch.object(guest, 'apt') as apt, \
                        patch.object(guest, 'run', side_effect=command):
                    with self.assertRaises(RuntimeError):
                        guest.native('node', '24')
                    if '24.22.1' not in available:
                        apt.assert_not_called()

    def test_additional_program_does_not_reuse_or_replace_vulnerable_node(self):
        for program in ('react', 'typescript', 'firebase'):
            with self.subTest(program=program), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = root / 'additional.json'
                config.write_text(json.dumps(dict(username='student', additive=True,
                    selections=[dict(id=program, version=BY_ID[program]['versions'][0])])), encoding='utf-8')
                def remap(value):
                    return config if str(value) == '/etc/uccloud-software-add.json' else root / str(value).lstrip('/')
                with patch.object(guest, 'Path', remap), \
                        patch.object(guest, 'pwd', SimpleNamespace(getpwnam=lambda _: SimpleNamespace(pw_dir=str(root)))), \
                        patch.object(guest.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), \
                        patch.object(guest, 'run', return_value='v24.22.0'), \
                        patch.object(guest, 'apt') as apt, patch.object(guest, 'update') as update, \
                        patch.object(guest, 'native') as native, \
                        patch.dict(os.environ, {'UCCLOUD_INSTALL_CONFIG': '/etc/uccloud-software-add.json'}):
                    before = config.read_bytes()
                    with self.assertRaises(RuntimeError):
                        guest.main()
                    self.assertEqual(before, config.read_bytes())
                    apt.assert_not_called(); update.assert_not_called(); native.assert_not_called()

    def test_generated_first_boot_and_additional_installer_include_guard(self):
        files = software_files('student', [dict(id='node', version='24')])
        script = next(item['content'] for item in files if item['path'].endswith('/uccloud-install-software'))
        self.assertIn('NODE_SECURITY_MINIMUM', script)
        self.assertIn('secure_node_package(', script)
        self.assertIn('require_safe_node(actual, version)', script)
        compile(script, 'guest-first-boot-installer', 'exec')
        additional = (Path(__file__).parents[1] / 'backend/software_install.py').read_text(encoding='utf-8')
        self.assertIn("'guest_software.py'", additional)


if __name__ == '__main__':
    unittest.main()
