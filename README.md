# recall

Describe a past coding-agent session from memory and get back its id and the command that reopens it. Works across Claude Code, Codex and Kimi Code.

```
recall "the one where I made my claude skills and hooks work in codex, then kimi"
```

```
 1. 188.26  claude 2026-09-10  tools   a122183c-3aed-48c9-a384-c57502e60429
      Open source LLM subscriptions comparison
      > So, I want to try out a new LLM subscription using the open source models...
      terms: agent claude codex harness hook kimi skill   turns: 13
 2. ...
 3. ...

1-3 resume   a agent search   d deep search   q quit
```

Press a number to reopen that session in the same window. If the right one is not there, press `a` and a model rereads the top ten. If that misses too, press `d` and claude searches the raw transcripts itself.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/how-it-works-dark.png">
  <img alt="your words go into a deterministic index and ranking over every transcript on disk, then an optional model call picks, and out comes the session id and its resume command" src="docs/how-it-works-light.png" width="780">
</picture>

Everything left of the model call is deterministic and runs in well under a second. Agent search adds one model call of a few seconds. Deep search runs a claude session over the raw transcripts and usually takes under a minute.

1. **index.** Every transcript on disk is read once into one record per session: its user prompts, title, project, branch, dates and the file paths the agent touched. Injected system text, tool results and hook output are stripped. Later runs reparse only files whose size or mtime changed.
2. **rank.** Your words are matched against every record. Rare words count more than common ones, a hit in the title or project name counts more than a hit in a late prompt, plurals and -ed/-ing are folded, and recent sessions get a small boost. Same words, same index, same answer every time.
3. **pick, only with agent search.** The top ten records go to a model along with each one's prompt list, in one call. The model reads what you asked for in each session and ranks up to three. It sees the prompts, not the titles, because a title comes from the first prompt and often names only how a session started.
4. **dig, only with deep search.** The index holds only what you typed, so a PR number or branch that an agent created is invisible to steps 2 and 3. Deep search runs claude with read-only tools over the raw transcripts. It looks things up on GitHub, greps for what the agent produced, and checks each answer in its transcript before it returns up to three. It is told which sessions you already saw.
5. **resume.** In a terminal, a number key reopens that session in the same window. Piped or with `-j`, the output ends with the command that reopens the first result. `-r` runs it without asking.

## Usage

```
recall "<what you remember>"           rank only, no model call. Three results and a key prompt in a terminal
recall <session-id>                    find that exact session
recall agent "<what you remember>"     rank, then one model call ranks up to three. claude sonnet by default
recall agent -a codex -m gpt-5 "..."   another agent and model do the ranking
recall deep "<what you remember>"      claude searches the raw transcripts and checks its answers. opus by default
recall -r "..."                        reopen the first result without asking

recall show <id>                       one session in full: prompts, paths, transcript file, resume command
recall resume <id>                     print the resume command
recall grep <regex>                    scan the raw transcripts, including assistant output
recall index                           refresh the index
recall stats                           sessions per harness, index location
```

Every option has a short and a long form. `recall -h` prints the full help.

In a terminal, every result list waits for one key:

| key | what it does |
|---|---|
| `1` to `3` | reopen that session here, in its own directory |
| `a` | agent search on the same words, after a plain search |
| `d` | deep search on the same words, after a plain or agent search |
| `q`, Enter, Esc | quit |

Piped output and `-j` never wait for a key, so scripts and agents get plain text or JSON.

A full session ID selects that session before text ranking. Search filters and exclusions still apply. Use `recall show <id-prefix>` for a shortened ID, and `recall grep '<words>'` for words from the agent's reply.

| option | what it does |
|---|---|
| `-r`, `--resume` | reopen the first result instead of waiting for a key |
| `-a`, `--agent` | who ranks in agent search: `claude`, `codex`, `kimi`, `local` (ollama). Deep search always runs on claude |
| `-m`, `--model` | model for agent search, or for deep search with `recall deep`, passed through as is |
| `-n`, `--limit` | how many results to print. 3 in a terminal, 10 when piped or with `-j` |
| `-f`, `--from` | only sessions from one harness |
| `-p`, `--project` | only sessions whose project directory or path contains this |
| `-s`, `--since`, `-u`, `--until` | date window, `YYYY-MM-DD`. Phrases like "last week" or "a few weeks ago" set one on their own |
| `-x`, `--exclude` | drop a session id prefix. An agent should pass its own session id |
| `-j`, `--json` | machine output |
| `-A`, `--all` | include sessions with no user prompt |

## Install

Needs a Rust toolchain. No runtime dependencies.

```
git clone https://github.com/otech47/recall
cd recall
cargo build --release
ln -s "$PWD/target/release/recall" ~/.local/bin/recall
```

The index lives at `~/.agents/recall/index.jsonl`. `RECALL_INDEX_DIR` moves it, `RECALL_AGENT` and `RECALL_MODEL` set the defaults for agent search, and `RECALL_DEEP_MODEL` sets the model for deep search.

## Tests

Run the search regressions against the release binary with Python's standard library:

```
cargo build --release
python3 tests/search.py
```

## For agents

`AGENTS.md` in this repo tells a coding agent how to install recall and how to use it. `skills/recall/SKILL.md` is the same guidance as a skill. Point your agent at the repo, or drop the skill into your skills directory.

<details>
<summary>what gets read, per harness</summary>

| harness | transcript | resume |
|---|---|---|
| Claude Code | `~/.claude/projects/<slug>/<id>.jsonl`, subagent `agent-*.jsonl` files skipped | `claude --resume <id>` |
| Codex | `~/.codex/sessions/Y/M/D/rollout-*.jsonl`, only threads a user started, names from `session_index.jsonl` | `codex resume <id>` |
| Kimi Code | `~/.kimi-code/sessions/wd_*/session_*/agents/main/wire.jsonl` plus `state.json` | `kimi --session <id>` |

Sessions in a workspace with `flake.nix` resume through `nix develop`, including sessions started in subdirectories. Fedi worktrees use the `worktree` shell.

Transcripts are read in place and never modified.

</details>

<details>
<summary>the ranking, exactly</summary>

Terms are lowercased, stopwords dropped, and stemmed with Porter steps 1a and 1b only. The scheme and host of a link are dropped first, so a pasted GitHub link is matched on its owner, repo and number. A term's rarity is the square of its BM25 idf, so a PR number that three sessions mention outweighs words that a third of all sessions share. For each session, score is the sum over matched terms of rarity times a field weight times log(1 + count), where the weights are title 4, project 3, first prompt 2.5, branch 2, compaction summary 1.5, touched paths 1.5, later prompts 1. The sum is scaled by the share of the query's total rarity that the session matched and by a 15 percent boost that decays over 90 days.

</details>

<details>
<summary>why the model call is optional, and when you want it</summary>

The ranking alone usually puts the right session in the top two. What it cannot do is tell a session that turned into the thing you describe apart from one whose title names it, because titles come from first prompts. `recall agent` fixes that with one call: the model gets the top ten candidates with up to twelve prompts each, about 4500 tokens, and is told the rank is a strong prior. Sonnet answers in a few seconds. Codex and Kimi work too but take longer to start in print mode.

</details>
