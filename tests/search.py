import json
import os
from pathlib import Path
import pty
import select
import subprocess
import time
import tempfile
import unittest


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('RECALL_TEST_DIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = Path(__file__).resolve().parents[1] / 'target/release/recall'
        self.env = dict(os.environ, RECALL_INDEX_DIR=str(self.root))

    def search(self, records, query, *options):
        (self.root / 'index.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in records))
        result = subprocess.run([str(self.binary), query, '-N', '-j', *options], env=self.env,
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def record(self, sid, **fields):
        return dict({'id': sid, 'harness': 'claude', 'kind': 'user', 'turns': 1,
                     'project': 'tools', 'ended': '2026-09-30T12:00:00Z'}, **fields)

    def test_exact_id_wins_over_a_prompt_that_mentions_it(self):
        sid = 'd45f57a0-098c-48a9-9058-c0d2d7af35da'
        records = [self.record('different-session', first='Find ' + sid), self.record(sid)]
        self.assertEqual([row['id'] for row in self.search(records, sid)], [sid])

    def test_all_four_agent_ids_support_case_and_whitespace(self):
        for agent, sid in [('claude', 'd45f57a0-098c-48a9-9058-c0d2d7af35da'),
                           ('codex', '01a0f1d5-3998-7c62-9bee-26921cfd4466'),
                           ('kimi', 'session_09815094-faa0-456d-bfe9-9f2e299563d1'),
                           ('cursor', 'bb93d387-d309-439b-a24b-b381625bd1ed')]:
            with self.subTest(agent=agent):
                records = [self.record(sid, harness=agent)]
                self.assertEqual(self.search(records, ' ' + sid.upper() + ' ')[0]['id'], sid)

    def test_id_lookup_obeys_exclusions_and_filters(self):
        sid = 'd45f57a0-098c-48a9-9058-c0d2d7af35da'
        records = [self.record(sid)]
        for options in [('-x', sid), ('-f', 'codex'), ('-p', 'another-project'), ('-n', '0')]:
            with self.subTest(options=options):
                self.assertEqual(self.search(records, sid, *options), [])

    def test_background_sessions_with_a_prompt_are_searched(self):
        records = [self.record('bg-session', kind='background', title='web release paper trail')]
        self.assertEqual([row['id'] for row in self.search(records, 'web release paper trail')], ['bg-session'])

    def test_sessions_without_a_prompt_need_all(self):
        records = [self.record('empty-session', turns=0, title='web release paper trail')]
        self.assertEqual(self.search(records, 'web release paper trail'), [])
        self.assertEqual([row['id'] for row in self.search(records, 'web release paper trail', '-A')], ['empty-session'])

    def test_pr_number_beats_sessions_that_share_only_the_link(self):
        link = 'https://github.com/fedibtc/fedi/pull/'
        records = [self.record('creator', project='fedi', prompts=['start', 'otherwise 12279 seems fine'])]
        records += [self.record(f'other-{n}', project='fedi', title='fedi pull requests',
                                first=f'{link}{n} and {link}{n + 1}') for n in range(11880, 11890)]
        records += [self.record(f'unrelated-{n}', title=f'topic {n}') for n in range(20)]
        self.assertEqual(self.search(records, 'the session that produced ' + link + '12279')[0]['id'], 'creator')

    def test_piped_and_json_output_keep_ten_results(self):
        records = [self.record(f'session-{n}', title='web release') for n in range(12)]
        self.assertEqual(len(self.search(records, 'web release')), 10)
        result = subprocess.run([str(self.binary), 'web release', '-N'], env=self.env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.count(' turns: '), 10)
        self.assertIn('resume: ', result.stdout)

    def test_model_searches_are_off_inside_a_deep_search(self):
        (self.root / 'index.jsonl').write_text(json.dumps(self.record('one', title='web release')) + '\n')
        for mode in ['agent', 'deep']:
            with self.subTest(mode=mode):
                result = subprocess.run([str(self.binary), mode, 'web release', '-N'], env=dict(self.env, RECALL_DEPTH='1'),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)

    def press(self, keys, query):
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(str(self.binary), [str(self.binary), query, '-N'], self.env)
        out, deadline = b'', time.time() + 10
        while time.time() < deadline:
            if select.select([fd], [], [], 0.1)[0]:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
            if keys and b'q quit' in out:
                time.sleep(0.2)
                os.write(fd, keys.encode())
                keys = ''
        os.waitpid(pid, 0)
        return out.decode()

    def test_terminal_shows_three_and_a_number_key_resumes(self):
        scripts = {'bash': 'exec /bin/bash --noprofile --norc "$@"\n',
                   'claude': 'printf "RESUMED %s\\n" "$2"\n',
                   'nix': '[ "$1" = develop ] && [ "$3" = -c ] || exit 1\nprintf "ENTERED_NIX %s\\n" "$2"\nshift 3\nexec "$@"\n'}
        for name, source in scripts.items():
            path = self.root / name
            path.write_text('#!/bin/sh\n' + source)
            path.chmod(0o755)
        self.env['PATH'] = str(self.root) + os.pathsep + os.environ['PATH']
        (self.root / 'flake.nix').touch()
        records = [self.record(f'session-{n}', cwd=str(self.root), title='web release') for n in range(5)]
        (self.root / 'index.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in records))
        screen = self.press('x2', 'web release')
        self.assertEqual(screen.count(' turns: '), 3)
        self.assertIn('1-3 resume   a agent search   d deep search   q quit', screen)
        second = screen.split(' 2. ')[1].splitlines()[0].split()[-1]
        self.assertIn('ENTERED_NIX ' + str(self.root.resolve()), screen)
        self.assertIn('RESUMED ' + second, screen)

    def test_text_search_still_ranks_the_matching_topic(self):
        records = [self.record('topic-session', title='writing audit review'),
                   self.record('different-session', title='terminal colors')]
        self.assertEqual([row['id'] for row in self.search(records, 'writing audit review')], ['topic-session'])


if __name__ == '__main__':
    unittest.main()
