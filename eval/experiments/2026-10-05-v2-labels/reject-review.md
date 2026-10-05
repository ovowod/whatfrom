# 명시적 오답 검토 표 (ticket 04)

라벨 고정 입력, 추천 단계 미호출로 돌린 보강 전 결과(`eval/experiments/2026-10-05-v2-labels/before.json`)에서 뽑았다.
검색 1위 대조군 27/40, 명시적 오답 후보 문항 16/40, accept에도 reject에도 없는 후보 231개다.

판단 기준: 질문의 필수 요구(검색 조건 또는 판단 요구)를 어긴다고 문서가 말하는 태그만 reject다.
선호, 권장하지 않음, 질문이 요구하지 않은 버전 고정은 reject가 아니다. accept 태그와 digest가 같은 후보는 넣지 않는다(목록의 후보는 모두 digest가 다르다).

## A. reject에 더할 태그 (판단 요구 위반)

| 문항 | 더할 태그 | 어긴 요구, 근거 |
| --- | --- | --- |
| python-ml-torch | 3.12-alpine, 3.11-alpine, 3.10-alpine | torch는 musllinux 휠이 없다. 기존 3.14·3.13-alpine reject와 같은 근거 |
| node-lts-prod | 26-alpine, 26-bookworm, 26-bullseye, 26-alpine3.23, 26-bookworm-slim | 26은 Current라 LTS가 아니다. 기존 node:26 reject와 같은 근거 |
| node-native-addon | 26-slim, 22-slim, 26-bookworm-slim, 24-trixie-slim, 22-trixie-slim, 26-bullseye-slim, 24-bullseye-slim, 22-bullseye-slim | slim은 node-gyp 툴체인이 없다(공식 README). 기존 24-slim reject와 같은 근거 |
| node-arm64-graviton | 26-alpine3.23, 24-alpine3.23, 22-alpine3.23 | arm64 musl 빌드는 실험 등급. 기존 alpine reject와 같은 근거 |
| postgres-prod-default | 14-alpine, 14-bookworm, 14-alpine3.23 | 14는 2026-11-12에 지원 종료. 기존 postgres:14 reject와 같은 근거 |
| postgres-postgis-extension | 15-alpine, 14-alpine, 18·17·16·15·14-alpine3.23 | alpine은 PostGIS를 직접 컴파일해야 한다(공식 README). 기존 alpine reject와 같은 근거 |
| nginx-stable-branch | 1.31-otel, 1.31-perl, 1.31-alpine, 1.31-alpine-otel, 1.31-alpine-perl, 1.31-alpine-slim | 1.31은 mainline. 기존 1.31 reject와 같은 근거 |
| nginx-otel-tracing | 1.30, 1.30-perl, 1.30-alpine, 1.31-alpine-perl, 1.30-alpine-perl, 1.31-alpine-slim, 1.30-alpine-slim | otel 모듈은 `-otel` 변형에만 있다. 기존 1.31 reject와 같은 근거 |
| temurin-jdk-build-stage | 26-jre, 17-jre, 11-jre, 8-jre | JRE에는 javac가 없다. 기존 jre reject와 같은 근거 |

## B. 판단 요구를 더하고 reject도 더할 태그

rationale이 이미 근거를 적었지만, ticket 03의 판단 요구가 그 요구를 담지 못한 문항이다.

| 문항 | 더할 판단 요구 | 더할 태그 |
| --- | --- | --- |
| python-latest-stable | "must be the newest final Python release line" | 3.13·3.12·3.11·3.10 계열 16개(기본, slim, alpine, trixie, bookworm). 질문이 "최신 안정 버전"을 요구한다 |
| temurin-jdk-build-stage | "must be a JDK that current Gradle can run on (Java 17 or newer)" | 11, 8, 11-jammy, 8-jammy. Gradle 9.7은 JVM 17~26에서만 돈다(rationale, Gradle compatibility 문서) |

## C. reject에서 뺄 태그 (질문이 요구하지 않은 것)

뺀 이유는 각 문항의 rationale에 적는다. 허용 정답은 바꾸지 않는다.

| 문항 | 뺄 태그 | 이유 |
| --- | --- | --- |
| python-size-first | 3.14, 3.14-trixie, latest | 크기 선호 |
| python-small-cli | 3.14, 3.14-bookworm, latest | 크기 선호 |
| node-size-first | 24, 24-bookworm, lts, latest | 크기 선호 |
| redis-cache-sidecar | 8, 8-trixie, latest | 크기 선호 |
| nginx-static-spa | 1.31, 1.31-perl, 1.31-otel, latest | 크기 선호, 쓰지 않는 모듈 |
| debian-slim-runtime | 13, trixie, latest | 크기 선호. sid-slim, testing-slim은 판단 요구(released suite) 위반이라 남긴다 |
| python-slim-vs-full | 3.14-alpine, 3.14-windowsservercore | 묻지 않은 변형 |
| python-scientific-stack | 3.15.0a8-slim, latest | 질문이 정식 릴리스나 버전 고정을 요구하지 않는다 |
| postgres-ci-ephemeral | 19beta3-alpine, latest | 같은 이유 |
| node-alpine-vs-slim | latest | 같은 이유 |
| golang-multistage-build | latest | 같은 이유 |
| temurin-jre-runtime | latest | 같은 이유 |
| redis-persistence-docs | latest | 같은 이유 |
| alpine-static-binary | latest | 같은 이유. 질문은 크기만 요구한다. edge와 날짜 태그는 판단 요구(stable 브랜치) 위반이라 남긴다 |
| debian-codename-vs-number | stable, testing, sid, latest | 질문은 차이를 묻는다. 요구가 없다 |

## D. 판단이 갈리는 것 (제안: 그대로 둔다)

| 문항 | 태그 | 제안 |
| --- | --- | --- |
| python-fastapi-prod | latest, 3.15.0b1-slim, 3.15.0a8-slim | "프로덕션"이 정식 릴리스를 요구한다고 보고 prerelease는 남긴다. 판단 요구 "must be a final release, not a prerelease"를 더한다. latest는 C와 같은 이유로 뺀다 |
| postgres-prod-default | postgres:15 계열 | 2027-11 지원 종료라 "몇 년"에 못 미치지만, rationale이 오답이 아니라 accept에서만 뺐다. 그대로 둔다 |
| 이동 태그가 위반 사유인 latest | postgres-prod-default, ubuntu-lts-base, ubuntu-noble-pinned, python-latest-stable의 latest | 질문이 버전을 몇 년 유지하거나("이 버전으로 갈 거야"), 특정 릴리스나 버전이 드러나는 태그를 요구한다. 태그가 옮겨 가는 것 자체가 위반이라 남긴다 |
