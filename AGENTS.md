## Agent skills

### Issue tracker

Issues live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the five default triage roles (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`) as-is. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Writing

주석, docstring, 문서, 사용자에게 보이는 문자열에서 영어 단어를 음차하지 않는다 (repository O, 리포지토리 X).

- 새로 쓰거나 고쳐 쓰는 문장에는 바로 적용한다.
- 기존 음차어는 그 문장을 고쳐 쓸 때 함께 바꾼다. 같은 docstring의 다른 문장이나, 옮기기만 한 문장은 그대로 둔다.
- 용어 하나를 일괄 치환할 때는 그 용어만 바꾼다.
- 원문을 인용할 때는 그대로 둔다.
