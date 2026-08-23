import test from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';

import {
  decodePostback,
  encodePostback,
  formatDateLabel,
  formatSummary,
  mergeRosterWithRecords,
  nextWorkingDay,
  normalizeStatus,
  parseTextReport,
  proxyKey,
  twToday,
} from '../src/attendance.js';
import { buildAskMessages, verifySignature } from '../src/line.js';

const ROSTER = {
  version: 1,
  members: [
    { name: '科長', userId: 'U0000000000000000000000000000001', aliases: ['王科長'] },
    { name: '陳昱', userId: 'U0000000000000000000000000000002', aliases: [] },
    { name: '葉維展', userId: null, aliases: [] },
    { name: '王耀駿', userId: 'U0000000000000000000000000000004', aliases: [] },
  ],
};

/* ---------------------------------------------------------------- 日期 */

test('nextWorkingDay 跳過週末：週五問下週一', () => {
  // 2026-08-07 (五) 16:00 TW = 08:00 UTC
  assert.equal(nextWorkingDay(new Date('2026-08-07T08:00:00Z')), '2026-08-10');
});

test('nextWorkingDay 平日就是隔天', () => {
  assert.equal(nextWorkingDay(new Date('2026-08-03T08:00:00Z')), '2026-08-04');
});

test('nextWorkingDay 跨月', () => {
  // 2026-07-31 是週五 → 下週一 8/3
  assert.equal(nextWorkingDay(new Date('2026-07-31T08:00:00Z')), '2026-08-03');
});

test('nextWorkingDay 用台灣日曆換日，不是 UTC', () => {
  // UTC 8/3 16:30 已是 TW 8/4 00:30 → 下一個上班日是 8/5
  assert.equal(nextWorkingDay(new Date('2026-08-03T16:30:00Z')), '2026-08-05');
  // 同一 UTC 日的稍早（TW 仍是 8/3）→ 8/4
  assert.equal(nextWorkingDay(new Date('2026-08-03T15:30:00Z')), '2026-08-04');
});

test('twToday 以 +8 判定日期', () => {
  assert.equal(twToday(new Date('2026-08-03T15:59:00Z')), '2026-08-03');
  assert.equal(twToday(new Date('2026-08-03T16:00:00Z')), '2026-08-04');
});

test('formatDateLabel 產出中文星期', () => {
  assert.equal(formatDateLabel('2026-08-04'), '8月4日（二）');
  assert.equal(formatDateLabel('2026-08-10'), '8月10日（一）');
});

/* ------------------------------------------------------------ 狀態解析 */

test('normalizeStatus 只吃完全相符的關鍵字', () => {
  assert.deepEqual(normalizeStatus('在部'), { key: 'in_office', label: '在部' });
  assert.deepEqual(normalizeStatus(' 請假 '), { key: 'leave', label: '請假' });
  assert.deepEqual(normalizeStatus('出差'), { key: 'official', label: '公出' });
  assert.equal(normalizeStatus('我等一下到部裡'), null);
  assert.equal(normalizeStatus(''), null);
  assert.equal(normalizeStatus(undefined), null);
});

test('parseTextReport 忽略一般聊天', () => {
  assert.deepEqual(parseTextReport('今天中午要不要一起吃飯', ROSTER), []);
  assert.deepEqual(parseTextReport('收到，謝謝科長', ROSTER), []);
  assert.deepEqual(parseTextReport('明天會議改到十點', ROSTER), []);
});

test('parseTextReport 收整行關鍵字', () => {
  const [r] = parseTextReport('在部', ROSTER);
  assert.deepEqual(r.target, { type: 'self' });
  assert.equal(r.status.label, '在部');
});

test('parseTextReport 支援前綴 + 自訂狀態', () => {
  const [r] = parseTextReport('到工 在三大合署作業', ROSTER);
  assert.deepEqual(r.target, { type: 'self' });
  assert.deepEqual(r.status, { key: 'custom', label: '在三大合署作業' });
});

test('parseTextReport 支援代人回報（需在名冊內）', () => {
  const [r] = parseTextReport('葉維展-在三大合署作業', ROSTER);
  assert.deepEqual(r.target, { type: 'name', name: '葉維展' });
  assert.equal(r.status.label, '在三大合署作業');

  // 別名也認得
  const [alias] = parseTextReport('王科長：請假', ROSTER);
  assert.deepEqual(alias.target, { type: 'name', name: '科長' });
  assert.equal(alias.status.label, '請假');

  // 名冊外的姓名不予採計
  assert.deepEqual(parseTextReport('路人甲-在部', ROSTER), []);
});

test('parseTextReport 逐行解析，可一次貼整份', () => {
  const reports = parseTextReport(
    ['陳昱-在部', '葉維展-在三大合署作業', '（以上）'].join('\n'),
    ROSTER,
  );
  assert.equal(reports.length, 2);
  assert.deepEqual(
    reports.map((r) => r.target.name),
    ['陳昱', '葉維展'],
  );
});

test('postback data 可編可解，垃圾值回 null', () => {
  const data = encodePostback('2026-08-04', 'in_office');
  assert.deepEqual(decodePostback(data), {
    isoDate: '2026-08-04',
    status: { key: 'in_office', label: '在部' },
  });
  assert.equal(decodePostback('att|2026-08-04|bogus'), null);
  assert.equal(decodePostback('att|nope|in_office'), null);
  assert.equal(decodePostback('隨便打的字'), null);
  assert.equal(decodePostback(undefined), null);
});

/* -------------------------------------------------------------- 統整 */

function dayWith(records) {
  return { date: '2026-08-04', session: 'am', closed: false, records };
}

test('mergeRosterWithRecords 依名冊順序，userId 優先', () => {
  const merged = mergeRosterWithRecords(
    ROSTER,
    dayWith({
      U0000000000000000000000000000002: { statusKey: 'in_office', statusLabel: '在部' },
      U0000000000000000000000000000001: { statusKey: 'leave', statusLabel: '請假' },
    }),
  );
  assert.deepEqual(
    merged.rows.map((r) => [r.name, r.status]),
    [
      ['科長', '請假'],
      ['陳昱', '在部'],
      ['葉維展', '未回報'],
      ['王耀駿', '未回報'],
    ],
  );
});

test('mergeRosterWithRecords 沒有 userId 時退回顯示名稱比對', () => {
  const merged = mergeRosterWithRecords(
    ROSTER,
    dayWith({
      Uzzz: { statusKey: 'custom', statusLabel: '在三大合署作業', displayName: '葉維展' },
    }),
  );
  assert.equal(merged.rows[2].status, '在三大合署作業');
  assert.equal(merged.extras.length, 0);
});

test('mergeRosterWithRecords 認得代報紀錄', () => {
  const merged = mergeRosterWithRecords(
    ROSTER,
    dayWith({ [proxyKey('王耀駿')]: { statusKey: 'official', statusLabel: '公出' } }),
  );
  assert.equal(merged.rows[3].status, '公出');
});

test('mergeRosterWithRecords 把名冊外的人放到 extras', () => {
  const merged = mergeRosterWithRecords(
    ROSTER,
    dayWith({
      Unew: { statusKey: 'in_office', statusLabel: '在部', displayName: '新來的' },
    }),
  );
  assert.equal(merged.rows.every((r) => !r.reported), true);
  assert.deepEqual(merged.extras, [
    { name: '新來的', status: '在部', statusKey: 'in_office', reported: true },
  ]);
});

test('formatSummary 對齊人工版本的格式', () => {
  const merged = mergeRosterWithRecords(
    ROSTER,
    dayWith({
      U0000000000000000000000000000001: { statusKey: 'in_office', statusLabel: '在部' },
      U0000000000000000000000000000002: { statusKey: 'in_office', statusLabel: '在部' },
      [proxyKey('葉維展')]: { statusKey: 'custom', statusLabel: '在三大合署作業' },
    }),
  );
  assert.equal(
    formatSummary('2026-08-04', merged),
    [
      '8月4日（二）上午到工狀況',
      '科長-在部',
      '陳昱-在部',
      '葉維展-在三大合署作業',
      '王耀駿-未回報',
      '──────────',
      '在部 2｜在三大合署作業 1｜未回報 1',
    ].join('\n'),
  );
});

/* ------------------------------------------------------- 訊息 / 驗簽 */

test('buildAskMessages 產出 textV2 @全員 + Flex 按鈕', () => {
  const [head, card] = buildAskMessages('2026-08-04');
  assert.equal(head.type, 'textV2');
  assert.deepEqual(head.substitution.everyone, {
    type: 'mention',
    mentionee: { type: 'all' },
  });
  assert.match(head.text, /^\{everyone\} /);

  assert.equal(card.type, 'flex');
  const buttons = card.contents.footer.contents.filter((c) => c.type === 'button');
  assert.equal(buttons.length, 3);
  assert.deepEqual(
    buttons.map((b) => b.action.data),
    ['att|2026-08-04|in_office', 'att|2026-08-04|leave', 'att|2026-08-04|official'],
  );
  assert.equal(buttons.every((b) => b.action.type === 'postback'), true);
});

test('buildAskMessages 可降級成不含 mention 的純文字', () => {
  const [head] = buildAskMessages('2026-08-04', { mention: false });
  assert.equal(head.type, 'text');
  assert.equal(head.substitution, undefined);
  assert.doesNotMatch(head.text, /\{everyone\}/);
});

test('verifySignature 接受正確簽章、擋掉竄改', async () => {
  const secret = 'test-channel-secret';
  const body = JSON.stringify({ events: [{ type: 'join' }] });
  const sig = createHmac('sha256', secret).update(body).digest('base64');

  assert.equal(await verifySignature(body, sig, secret), true);
  assert.equal(await verifySignature(`${body} `, sig, secret), false);
  assert.equal(await verifySignature(body, sig, 'wrong-secret'), false);
  assert.equal(await verifySignature(body, 'bm90LWJhc2U2NA==', secret), false);
  assert.equal(await verifySignature(body, null, secret), false);
  assert.equal(await verifySignature(body, sig, undefined), false);
});
