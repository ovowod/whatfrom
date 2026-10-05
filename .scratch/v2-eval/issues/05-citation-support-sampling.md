# 05: 인용 뒷받침 표본 도구

**What to build:** 결과 JSON에 번호가 붙은 근거 목록을 저장하고, `whatfrom eval` 아래 하위 명령 두 개를 더한다. 표본 생성은 결과 파일 여러 개에서 검증을 통과한 (진술, 인용) 쌍을 파일(안)마다 뽑아, 설정 이름을 가린 YAML과 별도 key 파일을 만든다. 채점 집계는 사람이 `verdict: supported|unsupported|unclear`를 채운 YAML과 key 파일로 안마다 인용 뒷받침률과 합격 여부를 낸다.

spec: `.scratch/v2-eval/spec.md` (인용 뒷받침 표본 도구)

**Blocked by:** None (can start immediately)

**Status:** done

- [x] 결과 JSON의 문항 기록에 근거 목록(번호, repository, 섹션 제목, 출처 URL)이 저장된다. 본문은 넣지 않는다.
- [x] 진술은 단일 선택의 주장 문장이고, 이미지는 추천 이미지다. 검증에 실패한 인용은 뽑지 않는다.
- [x] 파일마다 최대 50쌍, 문항당 최대 2쌍. seed 인자(기본값 고정)가 같으면 같은 표본이 나온다.
- [x] 표본 항목: 익명 id, 진술, 이미지, 인용 문장, 근거의 repository와 섹션 제목, 빈 `verdict`. 항목 순서를 섞고, 설정 이름과 결과 파일 이름이 들어가지 않는다.
- [x] key 파일이 익명 id와 (결과 파일, 문항, 주장, 인용)을 잇고, 항목마다 `verdict`를 뺀 내용의 hash를 담는다.
- [x] 채점 집계: 안마다 뒷받침률(supported ÷ (supported + unsupported)), 채점 가능한 쌍 수, unclear 수, 빈 항목 수, 표본 쌍 수를 낸다.
- [x] 빈 항목, 세 어휘 밖의 값, id 중복, key 파일과 다른 id 집합(빠진 행 포함), 채점 칸 밖 내용 변경이 있으면 합격 여부를 내지 않는다.
- [x] 채점 가능한 쌍이 0이면 미측정, unclear가 표본 쌍의 10%를 넘으면 합격 여부를 보류한다. 표본이 50쌍보다 적으면 표시한다.
- [x] 합격선은 90%다. 45/50은 합격, 44/50은 불합격.
- [x] 코드는 `whatfrom.eval` 안에 두고 계층 규칙 테스트가 통과한다.
- [x] CLI seam에서, fake provider로 실제 `whatfrom eval`을 돌려 나온 결과 파일과 손으로 채운 YAML 텍스트로 위를 확인한다.
