/**
 * 到工統計的純函式核心。
 *
 * 這個檔案刻意不 import 任何 Worker / KV / LINE API 的東西，
 * 讓 `node --test` 可以零依賴直接測（repo 內 tests/*.js 也是同樣風格）。
 */

// 台灣沒有日光節約時間，固定 +8 即可（與 src/SendMessage.py 的 TW_TZ 一致）
export const TW_OFFSET_MINUTES = 8 * 60;

// 統計的是「上午」到工狀況；改成全日或下午時只需動這裡
export const SESSION_LABEL = '上午';

export const UNREPORTED_LABEL = '未回報';

/**
 * Flex 訊息上的按鈕。key 進 postback data，label 進統計清冊。
 * buttonLabel 只影響按鈕上的字（LINE 上限 20 字）。
 */
export const STATUS_OPTIONS = [
  { key: 'in_office', label: '在部', buttonLabel: '在部' },
  { key: 'leave', label: '請假', buttonLabel: '請假' },
  { key: 'official', label: '公出', buttonLabel: '公出／出差' },
];

/**
 * 文字回覆的關鍵字對照。只有「整則訊息完全等於」其中一個詞才算數
 * —— 否則群組日常聊天（「我等一下到」）會被誤收。
 */
const STATUS_KEYWORDS = new Map([
  ['在部', 'in_office'],
  ['在辦公室', 'in_office'],
  ['到', 'in_office'],
  ['到公', 'in_office'],
  ['到工', 'in_office'],
  ['請假', 'leave'],
  ['休假', 'leave'],
  ['病假', 'leave'],
  ['事假', 'leave'],
  ['特休', 'leave'],
  ['公出', 'official'],
  ['出差', 'official'],
  ['外出', 'official'],
  ['洽公', 'official'],
  ['受訓', 'official'],
  ['訓練', 'official'],
]);

// 「到工 在三大合署作業」這種前綴：其後整串當自訂狀態
const REPORT_PREFIXES = ['/到工', '/回報', '＃到工', '#到工', '到工', '回報'];

// 姓名與狀態之間允許的分隔符（全形半形都吃）
const NAME_SEPARATORS = /^([^\s\-－—:：]{1,10})\s*[-－—:：]\s*(.+)$/;

const STATUS_LABEL_BY_KEY = new Map(STATUS_OPTIONS.map((o) => [o.key, o.label]));

/** postback data 的編碼格式：att|<iso>|<statusKey> */
export function encodePostback(isoDate, statusKey) {
  return `att|${isoDate}|${statusKey}`;
}

export function decodePostback(data) {
  if (typeof data !== 'string') return null;
  const parts = data.split('|');
  if (parts.length !== 3 || parts[0] !== 'att') return null;
  const [, isoDate, statusKey] = parts;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(isoDate)) return null;
  const label = STATUS_LABEL_BY_KEY.get(statusKey);
  if (!label) return null;
  return { isoDate, status: { key: statusKey, label } };
}

/* ------------------------------------------------------------------ 日期 */

/**
 * 把 UTC 時刻換成台灣當地的日曆欄位。
 * 位移後用 getUTC* 讀取，避免 runner 本身的時區干擾。
 */
export function twDateParts(now = new Date()) {
  const shifted = new Date(now.getTime() + TW_OFFSET_MINUTES * 60000);
  return {
    y: shifted.getUTCFullYear(),
    m: shifted.getUTCMonth() + 1,
    d: shifted.getUTCDate(),
    dow: shifted.getUTCDay(), // 0 = 週日
  };
}

const pad2 = (n) => String(n).padStart(2, '0');

export function toISODate({ y, m, d }) {
  return `${y}-${pad2(m)}-${pad2(d)}`;
}

export function twToday(now = new Date()) {
  return toISODate(twDateParts(now));
}

const WEEKDAY_ZH = ['日', '一', '二', '三', '四', '五', '六'];

/**
 * 台灣時間的「下一個上班日」（只跳過六日，國定假日不處理 —— 見 README 的已知限制）。
 * 週五下午問的是下週一。
 */
export function nextWorkingDay(now = new Date()) {
  const { y, m, d } = twDateParts(now);
  // 用 UTC 當作無時區的日曆算術載體，不再牽涉時區
  const cursor = new Date(Date.UTC(y, m - 1, d));
  do {
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  } while (cursor.getUTCDay() === 0 || cursor.getUTCDay() === 6);
  return toISODate({
    y: cursor.getUTCFullYear(),
    m: cursor.getUTCMonth() + 1,
    d: cursor.getUTCDate(),
  });
}

/** '2026-08-04' → '8月4日（二）' */
export function formatDateLabel(isoDate) {
  const [y, m, d] = isoDate.split('-').map(Number);
  const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return `${m}月${d}日（${WEEKDAY_ZH[dow]}）`;
}

/* ------------------------------------------------------------ 訊息解析 */

/** 整則訊息完全等於關鍵字才回傳狀態，否則 null。 */
export function normalizeStatus(text) {
  if (typeof text !== 'string') return null;
  const cleaned = text.trim().replace(/\s+/g, '');
  if (!cleaned) return null;
  const key = STATUS_KEYWORDS.get(cleaned);
  if (!key) return null;
  return { key, label: STATUS_LABEL_BY_KEY.get(key) };
}

/** 自訂狀態（「在三大合署作業」）：不在關鍵字表內，原文照收。 */
function asStatus(raw) {
  const known = normalizeStatus(raw);
  if (known) return known;
  const label = raw.trim().replace(/\s+/g, ' ');
  if (!label || label.length > 30) return null;
  return { key: 'custom', label };
}

function matchRosterName(roster, candidate) {
  if (!candidate) return null;
  const needle = candidate.trim();
  for (const member of roster?.members ?? []) {
    if (member.name === needle) return member.name;
    if ((member.aliases ?? []).includes(needle)) return member.name;
  }
  return null;
}

/**
 * 解析一則群組文字訊息，回傳 0..n 筆回報。
 *
 * 支援三種寫法（逐行獨立解析，所以可以整份貼上代報）：
 *   1. 整行就是關鍵字            → 「在部」
 *   2. 前綴 + 自訂狀態            → 「到工 在三大合署作業」
 *   3. 姓名-狀態（需在名冊內）    → 「葉維展-在三大合署作業」
 *
 * 不符合的行一律忽略，聊天內容不會被誤收。
 */
export function parseTextReport(text, roster) {
  if (typeof text !== 'string') return [];
  const results = [];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;

    // 3. 姓名-狀態（先試，因為「陳昱-在部」的後半也會命中關鍵字）
    const named = NAME_SEPARATORS.exec(line);
    if (named) {
      const rosterName = matchRosterName(roster, named[1]);
      if (rosterName) {
        const status = asStatus(named[2]);
        if (status) {
          results.push({ target: { type: 'name', name: rosterName }, status, raw: line });
          continue;
        }
      }
    }

    // 2. 前綴 + 自訂狀態
    const prefix = REPORT_PREFIXES.find(
      (p) => line.startsWith(p) && line.length > p.length,
    );
    if (prefix) {
      const status = asStatus(line.slice(prefix.length));
      if (status) {
        results.push({ target: { type: 'self' }, status, raw: line });
        continue;
      }
    }

    // 1. 整行關鍵字
    const status = normalizeStatus(line);
    if (status) {
      results.push({ target: { type: 'self' }, status, raw: line });
    }
  }
  return results;
}

/* -------------------------------------------------------------- 統整輸出 */

/** 代報的紀錄用這個 key，才不會跟本人以 userId 為 key 的紀錄互相覆蓋。 */
export function proxyKey(name) {
  return `name:${name}`;
}

/**
 * 依名冊順序把當日紀錄配對起來。
 * 配對優先序：本人 userId > 代報（姓名）> LINE 顯示名稱／別名。
 */
export function mergeRosterWithRecords(roster, dayRecord) {
  const records = dayRecord?.records ?? {};
  const members = roster?.members ?? [];
  const used = new Set();
  const rows = [];

  for (const member of members) {
    let record = null;
    let key = null;

    if (member.userId && records[member.userId]) {
      key = member.userId;
    } else if (records[proxyKey(member.name)]) {
      key = proxyKey(member.name);
    } else {
      const names = [member.name, ...(member.aliases ?? [])];
      for (const [k, v] of Object.entries(records)) {
        if (used.has(k)) continue;
        if (v.displayName && names.includes(v.displayName)) {
          key = k;
          break;
        }
      }
    }

    if (key) {
      record = records[key];
      used.add(key);
    }

    rows.push({
      name: member.name,
      status: record ? record.statusLabel : UNREPORTED_LABEL,
      statusKey: record ? record.statusKey : 'unreported',
      reported: Boolean(record),
    });
  }

  // 名冊外的回報者（新同仁還沒補進名冊時仍看得到）
  const extras = [];
  for (const [k, v] of Object.entries(records)) {
    if (used.has(k)) continue;
    extras.push({
      name: v.displayName || v.name || k,
      status: v.statusLabel,
      statusKey: v.statusKey,
      reported: true,
    });
  }

  const counts = {};
  for (const row of [...rows, ...extras]) {
    counts[row.status] = (counts[row.status] ?? 0) + 1;
  }

  return { rows, extras, counts };
}

/**
 * 產出貼回群組的清冊，格式對齊使用者原本的人工版本：
 *
 *   8月4日（二）上午到工狀況
 *   科長-在部
 *   葉維展-在三大合署作業
 *   ──────────
 *   在部 12｜請假 1｜未回報 1
 */
export function formatSummary(isoDate, merged) {
  const lines = [`${formatDateLabel(isoDate)}${SESSION_LABEL}到工狀況`];
  for (const row of merged.rows) {
    lines.push(`${row.name}-${row.status}`);
  }
  for (const row of merged.extras) {
    lines.push(`${row.name}-${row.status}（名冊外）`);
  }
  const tally = Object.entries(merged.counts)
    .map(([label, n]) => `${label} ${n}`)
    .join('｜');
  if (tally) {
    lines.push('──────────');
    lines.push(tally);
  }
  return lines.join('\n');
}
