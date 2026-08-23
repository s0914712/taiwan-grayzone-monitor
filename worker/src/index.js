/**
 * LINE 到工統計助手（Cloudflare Worker）
 *
 *   fetch      → LINE webhook（join / leave / message / postback）＋ /admin 手動觸發
 *   scheduled  → 08:00 UTC (16:00 TW) 發問、12:00 UTC (20:00 TW) 統整
 *
 * 群組 ID 不需要人工設定：bot 被邀進群組時 LINE 送出 join 事件，
 * source.groupId 就寫進 KV，之後兩支 cron 對所有已註冊群組推播。
 */
import {
  decodePostback,
  formatDateLabel,
  formatSummary,
  mergeRosterWithRecords,
  nextWorkingDay,
  parseTextReport,
  proxyKey,
  SESSION_LABEL,
} from './attendance.js';
import {
  buildAskMessages,
  getGroupMemberProfile,
  pushMessages,
  pushText,
  replyText,
  verifySignature,
} from './line.js';
import {
  addGroup,
  clearOpenDate,
  ensureDayOpen,
  getDay,
  getGroups,
  getOpenDate,
  getRosterCached,
  getSeenMembers,
  putDay,
  recordStatus,
  rememberMember,
  removeGroup,
  setOpenDate,
} from './storage.js';
import { upsertMonthFile } from './github.js';

const CRON_ASK = '0 8 * * 1-5'; // 16:00 TW
const CRON_SUMMARY = '0 12 * * 1-5'; // 20:00 TW

const HELP_TEXT = [
  '到工統計助手指令：',
  '• 直接點訊息上的按鈕即可回報',
  '• 其他狀況打字回覆，例：到工 在三大合署作業',
  '• 代人回報：葉維展-在三大合署作業',
  '• /我是 — 查自己的 userId（建名冊用）',
  '• /名冊 — 匯出已知成員的 userId 清單',
  '• /提問 — 立刻發出回報訊息',
  '• /統計 — 立刻統整並公布',
].join('\n');

/* ------------------------------------------------------------ 共用流程 */

async function resolveDisplayName(env, groupId, userId) {
  const seen = await getSeenMembers(env, groupId);
  if (seen[userId]) return seen[userId];
  const profile = await getGroupMemberProfile(env, groupId, userId);
  const name = profile?.displayName ?? null;
  if (name) await rememberMember(env, groupId, userId, name);
  return name;
}

/** 對所有已註冊群組推播；textV2 @全員被退回時自動降級成普通文字重送。 */
async function broadcastAsk(env, isoDate) {
  const groups = await getGroups(env);
  if (!groups.length) {
    console.warn('尚未有任何群組註冊，略過發問');
    return { groups: 0 };
  }
  for (const groupId of groups) {
    const res = await pushMessages(env, groupId, buildAskMessages(isoDate));
    if (!res.ok) {
      console.warn(`textV2 @全員推播失敗（${res.status}），改用純文字重送`);
      await pushMessages(env, groupId, buildAskMessages(isoDate, { mention: false }));
    }
  }
  return { groups: groups.length };
}

async function runAsk(env, isoDate) {
  await ensureDayOpen(env, isoDate);
  await setOpenDate(env, isoDate);
  const result = await broadcastAsk(env, isoDate);
  console.log(`已發出 ${isoDate} 的到工回報（${result.groups} 個群組）`);
  return { date: isoDate, ...result };
}

async function runSummary(env, isoDate) {
  const day = await getDay(env, isoDate);
  if (!day) {
    console.warn(`${isoDate} 沒有回報紀錄，略過統整`);
    return { date: isoDate, skipped: true };
  }

  const roster = await getRosterCached(env);
  const merged = mergeRosterWithRecords(roster, day);
  const text = formatSummary(isoDate, merged);

  const groups = await getGroups(env);
  for (const groupId of groups) {
    await pushText(env, groupId, text);
  }

  day.closed = true;
  await putDay(env, day);
  await clearOpenDate(env, isoDate);

  const archived = await upsertMonthFile(env, isoDate, day, text);
  console.log(
    `${isoDate} 統整完成（${groups.length} 個群組，存檔 ${archived.ok ? 'OK' : '略過/失敗'}）`,
  );
  return { date: isoDate, groups: groups.length, archived };
}

/* -------------------------------------------------------- webhook 事件 */

async function handleJoin(env, event) {
  const groupId = event.source?.groupId;
  if (!groupId) return;
  await addGroup(env, groupId);
  console.log(`已註冊群組 ${groupId}`);
  if (event.replyToken) {
    await replyText(
      env,
      event.replyToken,
      [
        '已加入。',
        `之後每個上班日 16:00 會在此群詢問隔日${SESSION_LABEL}到工狀況，20:00 公布統計。`,
        '',
        HELP_TEXT,
      ].join('\n'),
    );
  }
}

async function handlePostback(env, event) {
  const groupId = event.source?.groupId;
  const userId = event.source?.userId;
  if (!groupId || !userId) return;
  await addGroup(env, groupId);

  const decoded = decodePostback(event.postback?.data);
  if (!decoded) return;

  const displayName = await resolveDisplayName(env, groupId, userId);
  const day = await recordStatus(env, decoded.isoDate, userId, {
    userId,
    displayName,
    statusKey: decoded.status.key,
    statusLabel: decoded.status.label,
    source: 'postback',
  });

  // 已統整完畢還來點的人，給一句回覆免得以為有記到；正常情況下不回訊息避免洗版
  if (!day && event.replyToken) {
    await replyText(
      env,
      event.replyToken,
      `${formatDateLabel(decoded.isoDate)}的回報已截止，請洽科長人工補登。`,
    );
  }
}

async function handleCommand(env, event, text) {
  const groupId = event.source.groupId;
  const userId = event.source?.userId;
  const command = text.split(/\s+/)[0];

  if (command === '/我是' || command === '/whoami') {
    const displayName = userId ? await resolveDisplayName(env, groupId, userId) : null;
    await replyText(
      env,
      event.replyToken,
      `顯示名稱：${displayName ?? '（取得失敗）'}\nuserId：${userId ?? '（取不到）'}`,
    );
    return true;
  }

  if (command === '/名冊' || command === '/roster') {
    const seen = await getSeenMembers(env, groupId);
    const entries = Object.entries(seen);
    if (!entries.length) {
      await replyText(env, event.replyToken, '目前還沒有記錄到任何成員，請各自送一次 /我是。');
      return true;
    }
    const lines = entries.map(
      ([id, name]) => `  { "name": "${name}", "userId": "${id}", "aliases": [] },`,
    );
    await replyText(
      env,
      event.replyToken,
      ['可貼進 data/attendance_roster.json 的 members：', ...lines].join('\n'),
    );
    return true;
  }

  if (command === '/提問') {
    const isoDate = nextWorkingDay();
    await runAsk(env, isoDate);
    return true;
  }

  if (command === '/統計') {
    const isoDate = (await getOpenDate(env)) ?? nextWorkingDay();
    const result = await runSummary(env, isoDate);
    if (result.skipped) {
      await replyText(env, event.replyToken, '目前沒有進行中的回報，請先用 /提問 發起。');
    }
    return true;
  }

  if (command === '/說明' || command === '/help') {
    await replyText(env, event.replyToken, HELP_TEXT);
    return true;
  }

  return false;
}

async function handleMessage(env, event) {
  const groupId = event.source?.groupId;
  const userId = event.source?.userId;
  if (!groupId || event.message?.type !== 'text') return;
  await addGroup(env, groupId);

  const text = event.message.text ?? '';
  if (text.startsWith('/') && (await handleCommand(env, event, text))) return;

  const isoDate = await getOpenDate(env);
  if (!isoDate) return; // 不在回報時段，群組聊天一律忽略

  const roster = await getRosterCached(env);
  const reports = parseTextReport(text, roster);
  if (!reports.length) return;

  const displayName = userId ? await resolveDisplayName(env, groupId, userId) : null;

  for (const report of reports) {
    const isSelf = report.target.type === 'self';
    if (isSelf && !userId) continue;
    const key = isSelf ? userId : proxyKey(report.target.name);
    await recordStatus(env, isoDate, key, {
      userId: isSelf ? userId : undefined,
      name: isSelf ? undefined : report.target.name,
      displayName: isSelf ? displayName : undefined,
      statusKey: report.status.key,
      statusLabel: report.status.label,
      source: isSelf ? 'text' : 'proxy',
      raw: report.raw,
    });
  }
}

async function handleEvent(env, event) {
  try {
    switch (event.type) {
      case 'join':
        return await handleJoin(env, event);
      case 'leave':
        return await removeGroup(env, event.source?.groupId);
      case 'postback':
        return await handlePostback(env, event);
      case 'message':
        return await handleMessage(env, event);
      default:
        return undefined;
    }
  } catch (e) {
    // 單一事件出錯不該讓整批 webhook 掛掉
    console.error(`處理 ${event.type} 事件失敗: ${e?.stack ?? e}`);
    return undefined;
  }
}

/* ------------------------------------------------------------ HTTP 入口 */

function adminAuthorized(request, env) {
  if (!env.ADMIN_KEY) return false;
  const key = new URL(request.url).searchParams.get('key') ?? '';
  if (key.length !== env.ADMIN_KEY.length) return false;
  let diff = 0;
  for (let i = 0; i < key.length; i += 1) {
    diff |= key.charCodeAt(i) ^ env.ADMIN_KEY.charCodeAt(i);
  }
  return diff === 0;
}

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === '/health') {
      return json({ ok: true, groups: (await getGroups(env)).length });
    }

    if (url.pathname === '/webhook') {
      if (request.method !== 'POST') return new Response('Method Not Allowed', { status: 405 });
      const rawBody = await request.text();
      const signature = request.headers.get('x-line-signature');
      if (!(await verifySignature(rawBody, signature, env.LINE_CHANNEL_SECRET))) {
        return new Response('Bad signature', { status: 401 });
      }
      let payload;
      try {
        payload = JSON.parse(rawBody);
      } catch {
        return new Response('Bad JSON', { status: 400 });
      }
      // LINE 對 webhook 有回應時限：先回 200，事件在背景處理
      ctx.waitUntil(
        Promise.all((payload.events ?? []).map((event) => handleEvent(env, event))),
      );
      return new Response('OK');
    }

    if (url.pathname.startsWith('/admin/')) {
      if (!adminAuthorized(request, env)) return new Response('Forbidden', { status: 403 });
      const date = url.searchParams.get('date') || nextWorkingDay();

      if (url.pathname === '/admin/ask') return json(await runAsk(env, date));
      if (url.pathname === '/admin/summary') return json(await runSummary(env, date));
      if (url.pathname === '/admin/state') {
        return json({
          openDate: await getOpenDate(env),
          groups: await getGroups(env),
          day: await getDay(env, date),
        });
      }
      if (url.pathname === '/admin/roster') {
        return json(await getRosterCached(env, { force: true }));
      }
    }

    return new Response('Not Found', { status: 404 });
  },

  async scheduled(event, env, ctx) {
    if (event.cron === CRON_ASK) {
      ctx.waitUntil(runAsk(env, nextWorkingDay(new Date(event.scheduledTime))));
      return;
    }
    if (event.cron === CRON_SUMMARY) {
      const isoDate =
        (await getOpenDate(env)) ?? nextWorkingDay(new Date(event.scheduledTime));
      ctx.waitUntil(runSummary(env, isoDate));
      return;
    }
    console.warn(`未預期的 cron: ${event.cron}`);
  },
};
