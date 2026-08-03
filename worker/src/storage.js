/**
 * Workers KV 存取層。
 *
 * KV 是「即時狀態」；永久存檔在 repo 的 data/attendance/<YYYY-MM>.json（見 github.js）。
 *
 * Keys:
 *   groups            → string[]（已註冊的 groupId）
 *   openDate          → 目前開放回報的日期（16:00 設定、20:00 清掉）
 *   day:<YYYY-MM-DD>  → 當日回報紀錄
 *   members:<groupId> → { userId: displayName }，/名冊 指令用
 *   roster:cache      → { fetchedAt, roster }
 */

const ROSTER_TTL_MS = 24 * 60 * 60 * 1000;

async function getJSON(env, key, fallback) {
  const raw = await env.ATTENDANCE.get(key);
  if (!raw) return fallback;
  try {
    return JSON.parse(raw);
  } catch (e) {
    console.error(`KV ${key} 解析失敗，改用預設值: ${e}`);
    return fallback;
  }
}

function putJSON(env, key, value) {
  return env.ATTENDANCE.put(key, JSON.stringify(value));
}

/* ---------------------------------------------------------------- 群組 */

export function getGroups(env) {
  return getJSON(env, 'groups', []);
}

export async function addGroup(env, groupId) {
  const groups = await getGroups(env);
  if (groups.includes(groupId)) return groups;
  const next = [...groups, groupId];
  await putJSON(env, 'groups', next);
  return next;
}

export async function removeGroup(env, groupId) {
  const groups = await getGroups(env);
  const next = groups.filter((g) => g !== groupId);
  if (next.length !== groups.length) await putJSON(env, 'groups', next);
  return next;
}

/* ------------------------------------------------------------ 當日紀錄 */

const dayKey = (isoDate) => `day:${isoDate}`;

export function getDay(env, isoDate) {
  return getJSON(env, dayKey(isoDate), null);
}

export function putDay(env, day) {
  return putJSON(env, dayKey(day.date), day);
}

/**
 * 確保當日紀錄存在且開放回報。
 * 已有紀錄時**保留既有回報**只把 closed 打開 —— 手動 /提問 重發訊息不該把大家的回報清空。
 */
export async function ensureDayOpen(env, isoDate) {
  const existing = await getDay(env, isoDate);
  const day = existing
    ? { ...existing, closed: false }
    : {
        date: isoDate,
        session: 'am',
        createdAt: new Date().toISOString(),
        closed: false,
        records: {},
      };
  await putDay(env, day);
  return day;
}

/* ------------------------------------------------------ 開放中的回報日 */

export function getOpenDate(env) {
  return env.ATTENDANCE.get('openDate');
}

export function setOpenDate(env, isoDate) {
  return env.ATTENDANCE.put('openDate', isoDate);
}

/** 只清掉指向同一天的指標，避免誤刪已經被新一輪 /提問 覆寫的值。 */
export async function clearOpenDate(env, isoDate) {
  const current = await getOpenDate(env);
  if (!isoDate || current === isoDate) await env.ATTENDANCE.delete('openDate');
}

/**
 * 寫入一筆回報。同一個 key 重複回報時以最後一次為準（改狀態就是再點一次）。
 * 回傳 null 表示當日尚未開放回報或已統整完畢。
 */
export async function recordStatus(env, isoDate, key, entry) {
  const day = await getDay(env, isoDate);
  if (!day || day.closed) return null;
  day.records[key] = { ...entry, at: new Date().toISOString() };
  await putDay(env, day);
  return day;
}

/* -------------------------------------------------- 群組成員顯示名稱快取 */

const membersKey = (groupId) => `members:${groupId}`;

export function getSeenMembers(env, groupId) {
  return getJSON(env, membersKey(groupId), {});
}

export async function rememberMember(env, groupId, userId, displayName) {
  if (!displayName) return;
  const seen = await getSeenMembers(env, groupId);
  if (seen[userId] === displayName) return;
  seen[userId] = displayName;
  await putJSON(env, membersKey(groupId), seen);
}

/* ---------------------------------------------------------------- 名冊 */

/**
 * 從 repo 的 raw.githubusercontent 抓名冊，KV 快取 24 小時。
 * 改名冊只要 commit 到 main，不需要重新部署 Worker。
 * 抓取失敗時沿用快取（過期也用），避免一次網路故障就讓統計整份消失。
 */
export async function getRosterCached(env, { force = false } = {}) {
  const cached = await getJSON(env, 'roster:cache', null);
  const fresh =
    cached && Date.now() - new Date(cached.fetchedAt).getTime() < ROSTER_TTL_MS;
  if (fresh && !force) return cached.roster;

  try {
    const resp = await fetch(`${env.ROSTER_URL}?t=${Date.now()}`, {
      headers: { 'User-Agent': 'taiwan-grayzone-monitor-attendance-bot' },
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const roster = await resp.json();
    if (!Array.isArray(roster?.members)) throw new Error('名冊缺少 members 陣列');
    await putJSON(env, 'roster:cache', {
      fetchedAt: new Date().toISOString(),
      roster,
    });
    return roster;
  } catch (e) {
    console.error(`名冊抓取失敗（${e}），沿用快取`);
    return cached?.roster ?? { version: 1, members: [] };
  }
}
