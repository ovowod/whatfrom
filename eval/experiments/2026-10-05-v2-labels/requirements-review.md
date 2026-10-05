# 판단 요구 라벨 검토 표 (ticket 03)

2026-10-05: A, B, D를 제안대로 반영했다. C는 ticket 04에서 정한다.

초안은 subagent가 질문만 보고 썼다. 아래는 그 초안을 문항의 expected_plan, conditions, reject, rationale과 대조한 결과다.

표시 기준: ① 필터로 거를 수 있는 요구 ② 선호 ③ 베이스에 이미 있거나 설치하면 되는 것을 요구함 ④ reject를 설명하는 요구가 없음 ⑤ 빌드 stage 문항을 런타임 요구로 씀.

## A. 초안을 고치자는 문항

| 문항 | 초안 | 표시 | 제안 |
| --- | --- | --- | --- |
| python-build-stage | must include C compilers and build tools to compile Python wheels | ③ | rationale이 "slim에 build-essential을 깔면 되므로 오답이 아니다"라고 적는다. 이 요구는 slim과 alpine을 불만족으로 만든다. accept에 없는 태그라 오판정은 아니지만, 근거와 어긋난다. **빼고 []** |
| debian-slim-runtime | must include ca-certificates | ③ | debian 이미지에는 ca-certificates가 없어 모든 후보가 불만족이 된다. 설치하면 되는 것이다(F16 교훈). **빼기** |
| golang-cgo-sqlite | must include a C compiler (gcc) and libc headers to build with CGO_ENABLED=1 | ③ | rationale이 alpine은 apk로 gcc를 깔면 되므로 오답이 아니라고 적는다. 기본 태그에는 이미 있다. **빼기**. 대신 Windows reject를 설명할 요구가 필요하다(B 참고) |

## B. 요구를 더하자는 문항 (④, 필터로 거를 수 없는 버전·릴리스 성격)

초안은 LTS, stable 브랜치, 정식 릴리스를 "버전 필터"로 보고 뺐다. 하지만 검색 조건에는 버전 접두어만 있다. LTS 여부, 정식 릴리스 여부, 운영체제 종류는 필터로 거를 수 없으므로 판단 요구다.

| 문항 | 설명할 reject | 추가 제안 |
| --- | --- | --- |
| node-lts-prod | node:26, node:current, node:latest | "must be a Node.js LTS release line" |
| ubuntu-lts-base | 26.10, devel, rolling, latest | "must be an Ubuntu LTS release" |
| nginx-stable-branch | 1.31, mainline, latest | "must be on the nginx stable branch, not mainline" |
| python-latest-stable | 3.15 RC, beta, alpha 태그 | "must be a final Python release, not an alpha, beta, or release candidate" |
| postgres-prod-default | 19beta3, 14(곧 지원 종료) | "must be a final release whose major version is supported for several more years" |
| node-arm64-graviton | alpine 변형 | "must use a Node.js build that is officially supported on arm64, not an experimental musl build" |
| golang-cgo-sqlite, golang-cross-compile-arm64 | windowsservercore, nanoserver | "must be a Linux image" |
| debian-slim-runtime | sid-slim, testing-slim | "must be a released Debian suite, not testing or sid" |
| alpine-static-binary | edge, 날짜 태그 | "must be a supported stable Alpine release branch, not edge" |

## C. 정책을 정해야 하는 reject (ticket 04와 겹침)

질문이 버전 고정이나 안정성을 요구하지 않았는데 `latest`, prerelease, 이동 태그를 reject로 둔 문항이 많다. 근거는 대개 "Docker 모범 사례가 버전 고정을 권한다"다. 권장하지 않는 것은 명시적 오답이 아니라는 원칙에 비춰 보면 선호에 가깝다.

- `latest`만 reject: node-alpine-vs-slim, golang-multistage-build, temurin-jre-runtime, redis-persistence-docs
- `latest`와 prerelease: python-fastapi-prod, python-scientific-stack, postgres-ci-ephemeral
- 묻지 않은 변형: python-slim-vs-full (alpine, windows)
- 이동하는 suite 이름: debian-codename-vs-number (stable, testing, sid, latest)
- 크기 선호: python-size-first, python-small-cli, node-size-first, redis-cache-sidecar, nginx-static-spa, debian-slim-runtime (debian:13 등)

## D. 초안 그대로 두는 문항

| 문항 | 요구 |
| --- | --- |
| python-numpy-arm64 | numpy must install from prebuilt wheels without compiling from source |
| python-scientific-stack | pandas and scipy must install from prebuilt wheels without compiling from source |
| python-ml-torch | pip install torch must succeed using prebuilt wheels without building from source |
| python-musl-explained | pip packages must install from prebuilt manylinux wheels without compiling from source |
| node-native-addon | node-gyp must compile native addons without installing extra packages |
| postgres-postgis-extension | must include the PostGIS extension |
| redis-persistence-docs | must support persisting data to a volume so it survives container restarts |
| nginx-otel-tracing | must include the OpenTelemetry (otel) module for exporting tracing spans |
| temurin-jdk-build-stage | must include a full JDK (javac) to compile and run tests |

나머지 문항은 빈 목록이다. reject가 필터(버전 접두어, 배포판, 아키텍처)로 설명되거나 질문에 요구가 없다.
