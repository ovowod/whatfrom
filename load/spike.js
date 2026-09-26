// 부하 기준선(F10): 평소 0.2 RPS → 스파이크 2 RPS → 회복 0.2 RPS.
// 설계: docs/superpowers/specs/2026-09-24-f10-load-baseline-design.md §7
import exec from 'k6/execution';
import http from 'k6/http';
import { Counter, Rate, Trend } from 'k6/metrics';

const SMOKE = __ENV.SMOKE === '1';
const BASE_URL = __ENV.BASE_URL || 'http://host.docker.internal:8000';
const LABEL = __ENV.LABEL || (SMOKE ? 'smoke' : 'run');

// 질문 순서가 고정돼야 두 실행이 같은 질문을 같은 순서로 받는다(질문마다 LLM 시간이 다르다).
const DATA = JSON.parse(open('./questions.json'));

// offset은 순서표에서 구간이 시작하는 위치다. 스파이크와 회복의 offset(40, 420)은 앞 구간의
// 예정 요청 수만큼 떨어져 두 구간이 같은 인덱스를 쓰지 않는다. constant-arrival-rate는
// 계획보다 한 번 더 돌 수 있는데, 회복 구간은 그 한 번이 순서표 끝(420+60=480)을 넘어 나머지
// 연산으로 처음(order[0])으로 돌아간다. 결정적이라 비교에는 영향이 없다.
// 요청 하나가 VU를 최대 300초 붙잡으므로 필요한 VU를 미리 만들어 둔다. 실행 중에 새로 만드는
// 속도는 도착 속도를 따라가지 못해, 모자라면 요청을 보내지 못하고 dropped로 버린다.
const PHASES = [
  { name: 'baseline', rate: 1, timeUnit: '5s', seconds: SMOKE ? 10 : 180, offset: 0, vus: 100, maxVUs: 100 },
  { name: 'spike', rate: 2, timeUnit: '1s', seconds: SMOKE ? 10 : 180, offset: 40, vus: 700, maxVUs: 700 },
  { name: 'recovery', rate: 1, timeUnit: '5s', seconds: SMOKE ? 10 : 300, offset: 420, vus: 100, maxVUs: 100 },
];
const WINDOWS = ['baseline', 'spike', 'recovery', 'drain'];
const OFFSET = Object.fromEntries(PHASES.map((p) => [p.name, p.offset]));

// 구간 경계(테스트 시작부터 초). 완료 구간을 가르는 데 쓴다.
const BOUNDS = [];
let elapsed = 0;
for (const phase of PHASES) {
  phase.start = elapsed;
  elapsed += phase.seconds;
  BOUNDS.push(elapsed);
}
const LOAD_END = elapsed;

const recommendSeconds = new Trend('recommend_seconds');
const recommendOk = new Rate('recommend_ok');
const requestsFinished = new Counter('requests_finished');
const responsesReceived = new Counter('responses_received');
const recommendationsSucceeded = new Counter('recommendations_succeeded');

// k6 요약은 임계값으로 참조한 하위 지표만 따로 보여 준다. 기준선을 재는 것이지 판정하는 게
// 아니므로 늘 참인 조건을 건다(지표 종류마다 쓸 수 있는 집계가 다르다).
const thresholds = {};
for (const phase of PHASES) {
  thresholds[`recommend_seconds{scenario:${phase.name}}`] = ['max>=0'];
  thresholds[`recommend_ok{scenario:${phase.name}}`] = ['rate>=0'];
  thresholds[`dropped_iterations{scenario:${phase.name}}`] = ['count>=0'];
}
for (const window of WINDOWS) {
  thresholds[`requests_finished{window:${window}}`] = ['count>=0'];
  thresholds[`responses_received{window:${window}}`] = ['count>=0'];
  thresholds[`recommendations_succeeded{window:${window}}`] = ['count>=0'];
}

export const options = {
  // 기본값에는 p99가 없다.
  summaryTrendStats: ['p(50)', 'p(95)', 'p(99)', 'max', 'count'],
  thresholds,
  scenarios: Object.fromEntries(
    PHASES.map((phase) => [
      phase.name,
      {
        executor: 'constant-arrival-rate',
        exec: 'recommend',
        rate: phase.rate,
        timeUnit: phase.timeUnit,
        duration: `${phase.seconds}s`,
        startTime: `${phase.start}s`,
        preAllocatedVUs: phase.vus,
        maxVUs: phase.maxVUs,
        // 요청 타임아웃(300s)보다 길어야, 구간이 끝나기 직전에 시작한 요청이 k6에 의해
        // 끊기지 않고 스스로 타임아웃(status 0, finished로 집계)될 때까지 기다린다.
        gracefulStop: '310s',
      },
    ]),
  ),
};

function windowAt(seconds) {
  for (let i = 0; i < BOUNDS.length; i++) {
    if (seconds < BOUNDS[i]) return PHASES[i].name;
  }
  return 'drain';
}

export function recommend() {
  const phase = exec.scenario.name;
  const index = DATA.order[(OFFSET[phase] + exec.scenario.iterationInTest) % DATA.order.length];
  const question = DATA.questions[index].question;

  const started = Date.now();
  const res = http.post(`${BASE_URL}/recommend`, JSON.stringify({ question }), {
    headers: { 'Content-Type': 'application/json' },
    timeout: '300s',
    tags: { name: 'recommend' },
  });
  recommendSeconds.add((Date.now() - started) / 1000);

  // 요청 종료 ⊃ 응답 수신 ⊃ 추천 성공. 타임아웃·연결 오류는 status 0이다.
  const window = windowAt(exec.instance.currentTestRunDuration / 1000);
  requestsFinished.add(1, { window });
  if (res.status !== 0) responsesReceived.add(1, { window });

  let ok = false;
  if (res.status === 200) {
    try {
      ok = JSON.parse(res.body).recommendation != null;
    } catch (_) {
      ok = false;
    }
  }
  recommendOk.add(ok);
  if (ok) recommendationsSucceeded.add(1, { window });
}

function metricValue(data, name, stat) {
  const metric = data.metrics[name];
  return metric ? metric.values[stat] : null;
}

export function handleSummary(data) {
  const testRunSeconds = data.state.testRunDurationMs / 1000;
  const windowSeconds = Object.fromEntries(PHASES.map((p) => [p.name, p.seconds]));
  windowSeconds.drain = Math.max(0, testRunSeconds - LOAD_END);

  const byStart = {};
  for (const phase of PHASES) {
    const trend = `recommend_seconds{scenario:${phase.name}}`;
    byStart[phase.name] = {
      n: metricValue(data, trend, 'count') || 0,
      p50: metricValue(data, trend, 'p(50)'),
      p95: metricValue(data, trend, 'p(95)'),
      p99: metricValue(data, trend, 'p(99)'),
      max: metricValue(data, trend, 'max'),
      success_rate: metricValue(data, `recommend_ok{scenario:${phase.name}}`, 'rate'),
      dropped: metricValue(data, `dropped_iterations{scenario:${phase.name}}`, 'count') || 0,
    };
  }

  // 구간별 처리량은 그 구간의 count ÷ 구간 길이로 직접 계산한다.
  // Counter의 rate는 테스트 전체 시간으로 나눈 값이라 구간별 분모가 아니다.
  const byCompletion = {};
  for (const window of WINDOWS) {
    const count = (name) => metricValue(data, `${name}{window:${window}}`, 'count') || 0;
    const seconds = windowSeconds[window];
    const responses = count('responses_received');
    const succeeded = count('recommendations_succeeded');
    byCompletion[window] = {
      seconds,
      finished: count('requests_finished'),
      responses,
      succeeded,
      responses_per_s: seconds > 0 ? responses / seconds : null,
      succeeded_per_s: seconds > 0 ? succeeded / seconds : null,
    };
  }

  const result = {
    label: LABEL,
    smoke: SMOKE,
    test_run_seconds: testRunSeconds,
    by_start: byStart,
    by_completion: byCompletion,
  };
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const text = JSON.stringify(result, null, 2);
  return { [`/load/results/${stamp}-${LABEL}.json`]: text, stdout: `${text}\n` };
}
