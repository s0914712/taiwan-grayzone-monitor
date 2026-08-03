/**
 * index.js 的整線測試：用記憶體版 KV + 攔截 fetch，把「發問 → 回報 → 統整」跑完整一遍。
 *
 * 純函式的細節在 attendance.test.js；這裡只驗接線 —— 驗簽、事件分派、KV 讀寫、
 * 推播內容、以及 textV2 被退回時的降級。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';

import worker from '../src/index.js';

const SECRET = 'unit-test-channel-secret';
const GROUP_ID = 'Ctestgroup000000000000000000000000';
const USER_A = 'U0000000000000000000000000000002'; // 陳昱
const USER_B = 'U0000000000000000000000000000009'; // 名冊外

const ROSTER = {
  version: 1,
  members: [
    { name: '科長', userId: null, aliases: [] },
    { name: '陳昱', userId: USER_A, aliases: [] },
    { name: '葉維展', userId: null, aliases: [] },
  ],
};

function memoryKV() {
  const store = new Map();
  return {
    store,
    get: async (k) => (store.has(k) ? store.get(k) : null),
    put: async (k, v) => void store.set(k, v),
    delete: async (k) => void store.delete(k),
  };
}

/** 攔截所有外呼：名冊給假資料，LINE / GitHub 只記錄不真的送。 */
function stubFetch({ failTextV2 = false } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const href = String(url);
    calls.push({ url: href, init, body: init.body ? JSON.parse(init.body) : null });

    if (href.includes('attendance_roster.json')) {
      return new Response(JSON.stringify(ROSTER), { status: 200 });
    }
    if (href.endsWith('/message/push')) {
      const usesTextV2 = JSON.parse(init.body).messages.some((m) => m.type === 'textV2');
      if (failTextV2 && usesTextV2) return new Response('{"message":"invalid"}', { status: 400 });
      return new Response('{}', { status: 200 });
    }
    if (href.includes('/member/')) {
      const userId = href.split('/member/')[1];
      const names = { [USER_A]: '陳昱', [USER_B]: '新來的' };
      return new Response(JSON.stringify({ displayName: names[userId] ?? '未知' }), {
        status: 200,
      });
    }
    if (href.includes('api.github.com')) {
      return init.method === 'PUT'
        ? new Response('{}', { status: 201 })
        : new Response('Not Found', { status: 404 });
    }
    return new Response('{}', { status: 200 });
  };
  return calls;
}

function makeEnv(kv) {
  return {
    ATTENDANCE: kv,
    LINE_CHANNEL_SECRET: SECRET,
    LINE_CHANNEL_ACCESS_TOKEN: 'test-token',
    ADMIN_KEY: 'admin-secret',
    GITHUB_REPO: 's0914712/taiwan-grayzone-monitor',
    GITHUB_TOKEN: 'gh-token',
    ROSTER_URL: 'https://raw.githubusercontent.com/x/y/main/data/attendance_roster.json',
  };
}

function makeCtx() {
  const pending = [];
  return { ctx: { waitUntil: (p) => pending.push(p) }, settle: () => Promise.all(pending) };
}

function webhookRequest(events, { secret = SECRET } = {}) {
  const body = JSON.stringify({ destination: 'Ubot', events });
  const signature = createHmac('sha256', secret).update(body).digest('base64');
  return new Request('https://worker.test/webhook', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-line-signature': signature },
    body,
  });
}

const groupSource = (userId) => ({ type: 'group', groupId: GROUP_ID, userId });

async function post(env, events, opts) {
  const { ctx, settle } = makeCtx();
  const resp = await worker.fetch(webhookRequest(events, opts), env, ctx);
  await settle();
  return resp;
}

const pushes = (calls) => calls.filter((c) => c.url.endsWith('/message/push'));

/* ------------------------------------------------------------------ */

test('簽章錯誤一律 401，且不處理任何事件', async () => {
  const kv = memoryKV();
  stubFetch();
  const resp = await post(makeEnv(kv), [{ type: 'join', source: groupSource() }], {
    secret: 'wrong-secret',
  });
  assert.equal(resp.status, 401);
  assert.equal(kv.store.has('groups'), false);
});

test('join 事件自動註冊群組並回覆說明', async () => {
  const kv = memoryKV();
  const calls = stubFetch();
  const resp = await post(makeEnv(kv), [
    { type: 'join', replyToken: 'rt', source: groupSource() },
  ]);
  assert.equal(resp.status, 200);
  assert.deepEqual(JSON.parse(kv.store.get('groups')), [GROUP_ID]);
  const reply = calls.find((c) => c.url.endsWith('/message/reply'));
  assert.match(reply.body.messages[0].text, /已加入/);
});

test('leave 事件把群組移除', async () => {
  const kv = memoryKV();
  kv.store.set('groups', JSON.stringify([GROUP_ID]));
  stubFetch();
  await post(makeEnv(kv), [{ type: 'leave', source: groupSource() }]);
  assert.deepEqual(JSON.parse(kv.store.get('groups')), []);
});

test('完整流程：發問 → 按鈕 + 打字 + 代報 → 統整並存檔', async () => {
  const kv = memoryKV();
  kv.store.set('groups', JSON.stringify([GROUP_ID]));
  const calls = stubFetch();
  const env = makeEnv(kv);

  // 16:00 發問
  const askResp = await worker.fetch(
    new Request('https://worker.test/admin/ask?key=admin-secret&date=2026-08-04'),
    env,
    makeCtx().ctx,
  );
  assert.equal(askResp.status, 200);
  assert.equal(kv.store.get('openDate'), '2026-08-04');
  const ask = pushes(calls).at(-1).body;
  assert.equal(ask.messages[0].type, 'textV2');
  assert.equal(ask.messages[1].type, 'flex');

  // 陳昱點按鈕
  await post(env, [
    {
      type: 'postback',
      replyToken: 'rt',
      source: groupSource(USER_A),
      postback: { data: 'att|2026-08-04|in_office' },
    },
  ]);

  // 名冊外的人打字回報自訂狀態
  await post(env, [
    {
      type: 'message',
      replyToken: 'rt',
      source: groupSource(USER_B),
      message: { type: 'text', text: '到工 在三大合署作業' },
    },
  ]);

  // 有人代葉維展回報
  await post(env, [
    {
      type: 'message',
      replyToken: 'rt',
      source: groupSource(USER_A),
      message: { type: 'text', text: '葉維展-請假' },
    },
  ]);

  // 一般聊天不得被誤收
  await post(env, [
    {
      type: 'message',
      replyToken: 'rt',
      source: groupSource(USER_A),
      message: { type: 'text', text: '明天會議改到十點喔' },
    },
  ]);

  const day = JSON.parse(kv.store.get('day:2026-08-04'));
  assert.deepEqual(Object.keys(day.records).sort(), [USER_A, USER_B, 'name:葉維展'].sort());

  // 20:00 統整
  const sumResp = await worker.fetch(
    new Request('https://worker.test/admin/summary?key=admin-secret&date=2026-08-04'),
    env,
    makeCtx().ctx,
  );
  assert.equal(sumResp.status, 200);

  const summaryText = pushes(calls).at(-1).body.messages[0].text;
  assert.equal(
    summaryText,
    [
      '8月4日（二）上午到工狀況',
      '科長-未回報',
      '陳昱-在部',
      '葉維展-請假',
      '新來的-在三大合署作業（名冊外）',
      '──────────',
      '未回報 1｜在部 1｜請假 1｜在三大合署作業 1',
    ].join('\n'),
  );

  // 收工後標記關閉、清掉開放日、寫回 repo
  assert.equal(JSON.parse(kv.store.get('day:2026-08-04')).closed, true);
  assert.equal(kv.store.has('openDate'), false);
  const put = calls.find((c) => c.url.includes('api.github.com') && c.init.method === 'PUT');
  assert.match(put.url, /data\/attendance\/2026-08\.json$/);
  assert.equal(put.body.branch, 'main');
});

test('不在回報時段時，符合關鍵字的訊息也不記錄', async () => {
  const kv = memoryKV();
  kv.store.set('groups', JSON.stringify([GROUP_ID]));
  stubFetch();
  await post(makeEnv(kv), [
    {
      type: 'message',
      replyToken: 'rt',
      source: groupSource(USER_A),
      message: { type: 'text', text: '在部' },
    },
  ]);
  assert.equal([...kv.store.keys()].some((k) => k.startsWith('day:')), false);
});

test('textV2 被退回時自動改用純文字重送', async () => {
  const kv = memoryKV();
  kv.store.set('groups', JSON.stringify([GROUP_ID]));
  const calls = stubFetch({ failTextV2: true });
  await worker.fetch(
    new Request('https://worker.test/admin/ask?key=admin-secret&date=2026-08-04'),
    makeEnv(kv),
    makeCtx().ctx,
  );
  const sent = pushes(calls);
  assert.equal(sent.length, 2);
  assert.equal(sent[0].body.messages[0].type, 'textV2');
  assert.equal(sent[1].body.messages[0].type, 'text');
});

test('/我是 回覆自己的 userId，/統計 在沒有進行中的回報時說明原因', async () => {
  const kv = memoryKV();
  kv.store.set('groups', JSON.stringify([GROUP_ID]));
  const calls = stubFetch();
  const env = makeEnv(kv);

  await post(env, [
    {
      type: 'message',
      replyToken: 'rt',
      source: groupSource(USER_A),
      message: { type: 'text', text: '/我是' },
    },
  ]);
  const whoami = calls.filter((c) => c.url.endsWith('/message/reply')).at(-1);
  assert.match(whoami.body.messages[0].text, new RegExp(USER_A));

  await post(env, [
    {
      type: 'message',
      replyToken: 'rt',
      source: groupSource(USER_A),
      message: { type: 'text', text: '/統計' },
    },
  ]);
  const stats = calls.filter((c) => c.url.endsWith('/message/reply')).at(-1);
  assert.match(stats.body.messages[0].text, /沒有進行中的回報/);
});

test('/admin/* 缺少或帶錯 key 一律 403', async () => {
  const env = makeEnv(memoryKV());
  stubFetch();
  for (const url of [
    'https://worker.test/admin/ask',
    'https://worker.test/admin/ask?key=wrong',
    'https://worker.test/admin/summary?key=admin-secre',
  ]) {
    const resp = await worker.fetch(new Request(url), env, makeCtx().ctx);
    assert.equal(resp.status, 403, url);
  }
});
