## Agent skills

### Issue tracker

Issues live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the five default triage roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`) as-is. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Writing

주석, docstring, 문서, 사용자에게 보이는 문자열에서 영어 단어를 음차하지 않는다 (repository O, 리포지토리 X). 기존 음차어는 그 코드를 고칠 때 함께 바꾼다.
