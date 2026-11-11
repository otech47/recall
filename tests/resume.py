import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('RECALL_TEST_DIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.binary = Path(__file__).resolve().parents[1] / 'target/release/recall'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, RECALL_INDEX_DIR=str(self.root),
                        PATH=str(self.bin) + os.pathsep + os.environ['PATH'])
        self.script('bash', '#!/bin/sh\nexec /bin/bash --noprofile --norc "$@"\n')
        self.script('nix', '''#!/usr/bin/env python3
import os, sys
assert sys.argv[1] == 'develop', sys.argv
assert sys.argv[3] == '-c', sys.argv
assert not any(k in os.environ for k in ('CARGO_BUILD_TARGET_DIR', 'CARGO_TARGET_DIR', 'TS_RS_EXPORT_DIR', 'REPO_ROOT'))
if os.environ.get('RECALL_NIX_FAILURE'):
    sys.exit(37)
os.environ['IN_NIX_SHELL'] = 'impure'
os.environ['RECALL_TEST_FLAKE'] = sys.argv[2]
os.execvp(sys.argv[4], sys.argv[4:])
''')
        for agent in ('claude', 'codex', 'kimi', 'agent'):
            self.script(agent, '''#!/usr/bin/env python3
import json, os, sys
print(json.dumps({'argv': [os.path.basename(sys.argv[0]), *sys.argv[1:]],
                  'cwd': os.getcwd(), 'flake': os.environ.get('RECALL_TEST_FLAKE'),
                  'nix': os.environ.get('IN_NIX_SHELL')}))
''')

    def script(self, name, source):
        path = self.bin / name
        path.write_text(source)
        path.chmod(0o755)

    def session(self, cwd, harness='claude'):
        record = dict(id='resume-test', harness=harness, cwd=str(cwd), project=cwd.name,
                      kind='user', turns=1, title='resume fixture',
                      resume='echo STALE-CACHED-COMMAND')
        (self.root / 'index.jsonl').write_text(json.dumps(record) + '\n')

    def recall(self, *args, check=True):
        return subprocess.run([str(self.binary), *args, '-N'], env=self.env,
                              capture_output=True, text=True, check=check)

    def workspace(self, name='fedi', flake=True):
        root = self.root / name
        root.mkdir()
        if flake:
            (root / 'flake.nix').touch()
        return root

    def resumed(self):
        return json.loads(self.recall('resume', 'resume-test', '-r').stdout)

    def test_all_harnesses_enter_the_workspace_shell(self):
        root = self.workspace()
        for harness, args in [('claude', ['claude', '--resume']),
                              ('codex', ['codex', 'resume']),
                              ('kimi', ['kimi', '--session'])]:
            with self.subTest(harness=harness):
                self.session(root, harness)
                result = self.resumed()
                self.assertEqual(result['argv'], args + ['resume-test'])
                self.assertEqual(result['cwd'], str(root))
                self.assertEqual(result['flake'], str(root))
                self.assertEqual(result['nix'], 'impure')

    def test_cached_commands_are_refreshed_in_every_output(self):
        self.session(self.workspace())
        command = self.recall('resume', 'resume-test').stdout.strip()
        self.assertIn('nix develop', command)
        self.assertNotIn('STALE', command)
        shown = json.loads(self.recall('show', 'resume-test', '-j').stdout)
        found = json.loads(self.recall('resume-test', '-j').stdout)
        self.assertEqual(shown['resume'], command)
        self.assertEqual(found[0]['resume'], command)

    def test_subdirectory_keeps_its_cwd_and_uses_the_nearest_flake(self):
        root = self.workspace("fedi's checkout")
        nested = root / 'nested'
        nested.mkdir()
        (nested / 'flake.nix').touch()
        cwd = nested / 'src'
        cwd.mkdir()
        self.session(cwd)
        result = self.resumed()
        self.assertEqual(result['cwd'], str(cwd))
        self.assertEqual(result['flake'], str(nested))

    def test_fedi_worktree_selects_its_worktree_shell(self):
        root = self.workspace('fedi worktree')
        (root / '.git').write_text('gitdir: /unused/.git/worktrees/fedi\n')
        (root / 'dev').mkdir()
        (root / 'dev/worktree-cache.py').touch()
        self.session(root)
        self.assertEqual(self.resumed()['flake'], str(root) + '#worktree')

    def test_main_checkout_keeps_the_default_shell(self):
        root = self.workspace()
        (root / '.git').mkdir()
        (root / 'dev').mkdir()
        (root / 'dev/worktree-cache.py').touch()
        self.session(root)
        self.assertEqual(self.resumed()['flake'], str(root))

    def test_existing_shell_does_not_reuse_another_checkouts_build_paths(self):
        self.env.update(IN_NIX_SHELL='impure', CARGO_BUILD_TARGET_DIR='/other/target',
                        CARGO_TARGET_DIR='/other/target', TS_RS_EXPORT_DIR='/other/bindings',
                        REPO_ROOT='/other')
        root = self.workspace()
        self.session(root)
        self.assertEqual(self.resumed()['flake'], str(root))

    def test_plain_workspace_does_not_enter_nix(self):
        self.session(self.workspace(flake=False))
        self.assertNotIn('nix develop', self.recall('resume', 'resume-test').stdout)
        self.assertIsNone(self.resumed()['flake'])

    def test_nix_failure_does_not_launch_the_agent(self):
        self.env['RECALL_NIX_FAILURE'] = '1'
        self.session(self.workspace())
        result = self.recall('resume', 'resume-test', '-r', check=False)
        self.assertEqual(result.returncode, 37)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
