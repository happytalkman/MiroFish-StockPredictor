/**
 * WeAidStock 클라우드 진입점 (Cloudflare Worker + Container)
 *
 * - 모든 요청을 단 하나의 컨테이너 인스턴스('main')로 전달 — Cloudflare Access 가 앞에서 로그인을 처리하고,
 *   컨테이너 안의 게이트가 Cf-Access-Jwt-Assertion 을 다시 검증합니다 (관리 API 는 원격에서 항상 403).
 * - 예약(cron) 5분마다: 평일 07:30~17:00 KST 에는 컨테이너를 깨워 둬서 안의 스케줄러(오늘의 테마 08:40,
 *   장중 가격 알림, 16:40 예측 채점, 선택 시 07:40 관심종목 분석)가 그대로 동작하게 함.
 *   그 밖의 시간에는 요청이 없으면 sleepAfter 뒤 멈춤 (비용 절감). 상태는 entrypoint 가 R2 에 스냅샷.
 */
import { Container, getContainer } from '@cloudflare/containers';
import { env as workerEnv } from 'cloudflare:workers';

// 컨테이너로 넘길 설정·비밀값 이름 (wrangler secret put / vars 로 넣은 것만 전달됨)
const PASS_THROUGH = [
  'XAI_API_KEY', 'AI_GATEWAY_API_KEY', 'LLM_PROVIDER', 'XAI_LLM_MODEL', 'XAI_FAST_MODEL', 'XAI_JUDGE_MODEL',
  'LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL_NAME', 'TYPESAFE_API_KEY', 'DART_API_KEY', 'ZEP_API_KEY',
  'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'REPORT_SPEED', 'PREFETCH_AFTER_REPORT', 'FLOW_SOURCE',
  'WATCHLIST_SCHEDULE', 'WATCHLIST_TIME', 'ALERTS', 'ALERT_INTERVAL', 'LLM_PRICES', 'USD_KRW',
  'REMOTE_ACCESS', 'CF_ACCESS_TEAM_DOMAIN', 'CF_ACCESS_AUD', 'REMOTE_ACCESS_EMAILS', 'CRON_SECRET',
  'R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET', 'R2_PREFIX', 'SNAPSHOT_EVERY',
] as const;

type Env = Record<(typeof PASS_THROUGH)[number], string | undefined> & {
  WEAID: DurableObjectNamespace<WeAidStock>;
};

function containerEnv(): Record<string, string> {
  const src = workerEnv as unknown as Record<string, unknown>;
  const out: Record<string, string> = {};
  for (const k of PASS_THROUGH) {
    const v = src[k];
    if (typeof v === 'string' && v !== '') out[k] = v;
  }
  return out;
}

export class WeAidStock extends Container<Env> {
  defaultPort = 8080;
  sleepAfter = '45m';
  pingEndpoint = 'container/health';
  envVars = containerEnv();
}

const INSTANCE = 'main';

function kstNow(ms: number) {
  const d = new Date(ms + 9 * 3600 * 1000);
  return { dow: d.getUTCDay(), minutes: d.getUTCHours() * 60 + d.getUTCMinutes() };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    // 예약 작업 경로는 외부에서 부를 수 없게 막음 (컨테이너 쪽도 비밀 헤더로 한 번 더 확인)
    if (url.pathname.startsWith('/api/internal/')) return new Response('Not found', { status: 404 });
    return getContainer(env.WEAID, INSTANCE).fetch(request);
  },

  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    const { dow, minutes } = kstNow(controller.scheduledTime);
    const weekday = dow >= 1 && dow <= 5;
    if (!weekday || minutes < 7 * 60 + 30 || minutes > 17 * 60) return;
    if (!env.CRON_SECRET) {
      console.warn('CRON_SECRET 이 없어 컨테이너를 깨우지 않습니다 (wrangler secret put CRON_SECRET)');
      return;
    }
    const req = new Request('http://container/api/internal/cron/wake', {
      method: 'POST',
      headers: { 'X-WeAid-Cron': env.CRON_SECRET },
    });
    ctx.waitUntil(getContainer(env.WEAID, INSTANCE).fetch(req).then((r) => {
      if (!r.ok) console.warn(`wake 실패: HTTP ${r.status}`);
    }));
  },
};
