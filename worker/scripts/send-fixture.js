#!/usr/bin/env node
/**
 * 本機測 webhook 用：對 `wrangler dev` 送出一個帶合法簽章的合成 LINE 事件。
 *
 *   cd worker && npx wrangler dev            # 另一個終端
 *   LINE_CHANNEL_SECRET=xxx node test/send-fixture.js join
 *   LINE_CHANNEL_SECRET=xxx node test/send-fixture.js postback 2026-08-04 in_office
 *   LINE_CHANNEL_SECRET=xxx node test/send-fixture.js text "到工 在三大合署作業"
 */
import { createHmac } from 'node:crypto';

const URL_BASE = process.env.WORKER_URL || 'http://127.0.0.1:8787';
const SECRET = process.env.LINE_CHANNEL_SECRET;
const GROUP_ID = process.env.TEST_GROUP_ID || 'Cfixturegroup00000000000000000000';
const USER_ID = process.env.TEST_USER_ID || 'Ufixtureuser00000000000000000000';

if (!SECRET) {
  console.error('請設定 LINE_CHANNEL_SECRET（要跟 wrangler dev 讀到的同一把）');
  process.exit(1);
}

const [kind, ...rest] = process.argv.slice(2);
const source = { type: 'group', groupId: GROUP_ID, userId: USER_ID };
const base = { timestamp: Date.now(), mode: 'active', replyToken: 'fixture-reply-token', source };

const EVENTS = {
  join: () => ({ ...base, type: 'join' }),
  leave: () => ({ ...base, type: 'leave' }),
  postback: () => ({
    ...base,
    type: 'postback',
    postback: { data: `att|${rest[0] ?? '2026-08-04'}|${rest[1] ?? 'in_office'}` },
  }),
  text: () => ({
    ...base,
    type: 'message',
    message: { type: 'text', id: '1', text: rest.join(' ') || '在部' },
  }),
};

if (!EVENTS[kind]) {
  console.error(`用法: node test/send-fixture.js <${Object.keys(EVENTS).join('|')}> [args]`);
  process.exit(1);
}

const body = JSON.stringify({ destination: 'Ufixturebot', events: [EVENTS[kind]()] });
const signature = createHmac('sha256', SECRET).update(body).digest('base64');

const resp = await fetch(`${URL_BASE}/webhook`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'x-line-signature': signature },
  body,
});
console.log(`${resp.status} ${await resp.text()}`);
