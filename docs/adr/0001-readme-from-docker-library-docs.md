# 0001. README 원본을 docker-library/docs에서 받는다

- Status: accepted
- Date: 2026-10-01 (결정은 PR #12에서 내렸고, 이 문서는 그 결정을 사후에 기록한다)
- Supersedes: `docs/superpowers/specs/2026-09-14-collection-expansion-design.md`의 "색인의 README 요청도 같은 `HubClient`를 써서 rate limit 처리를 받는다"

## Context

색인은 공식 image의 README를 chunk로 나눠 embedding한다. 처음에는 Docker Hub repository API의 `full_description`을 README로 썼다.

그런데 `full_description`은 25,000자에서 잘린다. postgres는 Image Variants 절이 통째로 빠지고, eclipse-temurin은 tag 목록이 줄어든다. 추천 근거로 쓰는 절이 사라지면 검색 결과도 나빠진다.

Hub 본문은 docker-library/docs repository의 README를 올린 것이다. 그래서 원본을 받으면 잘리지 않은 같은 문서를 얻을 수 있다.

## Decision

- `index`는 README를 `https://raw.githubusercontent.com/docker-library/docs/master/<repository>/README.md`에서 받는다.
- 근거 chunk의 출처 URL은 사람이 여는 GitHub 화면(`github.com/docker-library/docs/blob/master/<repository>/README.md`)을 쓴다.
- 원본을 받지 못해도 Hub 본문으로 대체하지 않는다. 그 repository의 색인을 실패로 처리한다. 잘린 문서로 조용히 색인되는 것보다 실패가 드러나는 편이 낫다.
- 이 요청은 `HubClient`를 거치지 않고 `httpx2`로 직접 보낸다. 재시도와 rate limit 처리는 하지 않는다.

## Consequences

- `HubClient`의 재시도(429, 500, 502, 503, 504)와 rate limit 대기를 받지 않는다. 재시도를 넣지 않은 이유는 다음과 같다.
  - `index`는 사람이 수동으로 다시 돌리는 batch다. 기다리는 사용자가 없고, 실패하면 다시 실행하면 된다.
  - `index --all`은 repository별로 실패를 격리하고 종료 코드 1로 알린다. 한 repository가 실패해도 나머지는 색인된다.
  - 대상은 공식 image README 10개라 GitHub raw의 rate limit에 걸릴 만큼 요청하지 않는다.
- 색인은 Docker Hub와 GitHub 두 곳에 의존하게 된다. repository의 tag와 metadata는 계속 Hub에서 받는다.
- docker-library/docs의 경로 구조(`<repository>/README.md`)가 바뀌면 색인이 실패한다. 이 실패는 위 규칙대로 드러난다.

다음 경우에는 이 결정을 다시 본다: 색인 대상이 크게 늘어 rate limit이 실제로 문제가 될 때, 또는 `index`가 사람 없이 정기적으로 돌게 될 때.
