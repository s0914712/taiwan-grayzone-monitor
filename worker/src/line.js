/**
 * LINE Messaging API client + 訊息建構器。
 *
 * 只用 WebCrypto / fetch，所以 `node --test` 能直接測簽章驗證（Node 18+ 內建 globalThis.crypto）。
 */
import {
  STATUS_OPTIONS,
  SESSION_LABEL,
  encodePostback,
  formatDateLabel,
} from './attendance.js';

const LINE_API = 'https://api.line.me/v2/bot';

/* ---------------------------------------------------------------- 驗簽 */

function base64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/** 長度無關的定時比較，避免用 === 洩漏簽章前綴。 */
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a[i] ^ b[i];
  return diff === 0;
}

/**
 * 驗證 LINE 的 x-line-signature：HMAC-SHA256(channelSecret, rawBody) 的 base64。
 * rawBody 必須是「原封不動的」請求字串 —— 不能先 JSON.parse 再 stringify。
 */
export async function verifySignature(rawBody, signature, channelSecret) {
  if (!signature || !channelSecret) return false;
  let expected;
  try {
    expected = base64ToBytes(signature);
  } catch {
    return false;
  }
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(channelSecret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const mac = new Uint8Array(
    await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(rawBody)),
  );
  return timingSafeEqual(mac, expected);
}

/* ------------------------------------------------------------- API 呼叫 */

async function lineFetch(env, path, init) {
  const resp = await fetch(`${LINE_API}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`,
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });
  const body = await resp.text();
  if (!resp.ok) {
    console.error(`LINE ${path} ${resp.status}: ${body}`);
  }
  return { ok: resp.ok, status: resp.status, body };
}

export function pushMessages(env, to, messages) {
  return lineFetch(env, '/message/push', {
    method: 'POST',
    body: JSON.stringify({ to, messages }),
  });
}

export function replyMessage(env, replyToken, messages) {
  return lineFetch(env, '/message/reply', {
    method: 'POST',
    body: JSON.stringify({ replyToken, messages }),
  });
}

export function pushText(env, to, text) {
  return pushMessages(env, to, [{ type: 'text', text }]);
}

export function replyText(env, replyToken, text) {
  return replyMessage(env, replyToken, [{ type: 'text', text }]);
}

/**
 * 群組成員的顯示名稱。不需要加好友，但成員退群後會 404 —— 失敗回 null，呼叫端自行降級。
 */
export async function getGroupMemberProfile(env, groupId, userId) {
  const { ok, body } = await lineFetch(env, `/group/${groupId}/member/${userId}`, {
    method: 'GET',
  });
  if (!ok) return null;
  try {
    return JSON.parse(body);
  } catch {
    return null;
  }
}

/* --------------------------------------------------------- 訊息建構器 */

/**
 * 16:00 的提問訊息。
 *
 * mention=true 時第一則用 textV2 + {everyone} @全員（僅群組／多人聊天室支援）。
 * 若該帳號不支援會被 API 退回 400，呼叫端改用 mention=false 重送 —— 功能不變，只少了通知。
 */
export function buildAskMessages(isoDate, { mention = true } = {}) {
  const label = formatDateLabel(isoDate);
  const prompt = `請回報 ${label}${SESSION_LABEL}到工狀況，20:00 統整。`;

  const head = mention
    ? {
        type: 'textV2',
        text: `{everyone} ${prompt}`,
        substitution: {
          everyone: { type: 'mention', mentionee: { type: 'all' } },
        },
      }
    : { type: 'text', text: prompt };

  const buttons = STATUS_OPTIONS.map((option) => ({
    type: 'button',
    style: option.key === 'in_office' ? 'primary' : 'secondary',
    height: 'sm',
    action: {
      type: 'postback',
      label: option.buttonLabel,
      data: encodePostback(isoDate, option.key),
      displayText: option.label,
    },
  }));

  const card = {
    type: 'flex',
    altText: prompt,
    contents: {
      type: 'bubble',
      body: {
        type: 'box',
        layout: 'vertical',
        spacing: 'sm',
        contents: [
          { type: 'text', text: '到工回報', weight: 'bold', size: 'lg' },
          {
            type: 'text',
            text: `${label}${SESSION_LABEL}`,
            size: 'md',
            color: '#555555',
          },
          {
            type: 'text',
            text: '請各自點選狀態（按鈕會留在訊息裡，不會被其他人的發言蓋掉）',
            size: 'xs',
            color: '#888888',
            wrap: true,
          },
        ],
      },
      footer: {
        type: 'box',
        layout: 'vertical',
        spacing: 'sm',
        contents: [
          ...buttons,
          {
            type: 'text',
            text: '其他狀況請直接打字，例：在三大合署作業',
            size: 'xs',
            color: '#888888',
            wrap: true,
            margin: 'md',
          },
        ],
      },
    },
  };

  return [head, card];
}
