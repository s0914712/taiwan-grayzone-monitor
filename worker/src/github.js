/**
 * 把每日統計存回 repo（GitHub Contents API）。
 *
 * 流程與既有的 src/publish_threads.py:_upload_single_file_to_github 相同：
 * 先 GET 取既有 blob 的 sha，再 PUT base64 內容（有 sha 就是更新、沒有就是新建）。
 * 只有 20:00 那一支 cron 會寫，沒有併發競爭。
 */

function b64encodeUtf8(str) {
  const bytes = new TextEncoder().encode(str);
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

function b64decodeUtf8(b64) {
  const binary = atob(b64.replace(/\s/g, ''));
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function ghHeaders(env) {
  return {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'taiwan-grayzone-monitor-attendance-bot',
  };
}

/**
 * 把當日紀錄併進 data/attendance/<YYYY-MM>.json。
 * GITHUB_TOKEN 未設定時直接跳過（Worker 仍會推播，只是不留 repo 存檔）。
 */
export async function upsertMonthFile(env, isoDate, dayRecord, summaryText) {
  if (!env.GITHUB_TOKEN) {
    console.warn('未設定 GITHUB_TOKEN，略過 repo 存檔');
    return { ok: false, skipped: true };
  }

  const month = isoDate.slice(0, 7);
  const path = `data/attendance/${month}.json`;
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/${path}`;

  let existing = { month, days: {} };
  let sha;
  const getResp = await fetch(url, { headers: ghHeaders(env) });
  if (getResp.ok) {
    const meta = await getResp.json();
    sha = meta.sha;
    try {
      const parsed = JSON.parse(b64decodeUtf8(meta.content));
      if (parsed && typeof parsed.days === 'object') existing = parsed;
    } catch (e) {
      console.error(`${path} 內容無法解析，改為重建: ${e}`);
    }
  } else if (getResp.status !== 404) {
    console.error(`讀取 ${path} 失敗: ${getResp.status} ${await getResp.text()}`);
    return { ok: false, status: getResp.status };
  }

  existing.month = month;
  existing.days[isoDate] = {
    date: isoDate,
    session: dayRecord.session ?? 'am',
    summary: summaryText,
    records: dayRecord.records ?? {},
    closedAt: new Date().toISOString(),
  };

  const putResp = await fetch(url, {
    method: 'PUT',
    headers: { ...ghHeaders(env), 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: `📋 到工統計 ${isoDate}`,
      content: b64encodeUtf8(`${JSON.stringify(existing, null, 2)}\n`),
      branch: env.GITHUB_BRANCH || 'main',
      ...(sha ? { sha } : {}),
    }),
  });

  if (!putResp.ok) {
    console.error(`寫入 ${path} 失敗: ${putResp.status} ${await putResp.text()}`);
    return { ok: false, status: putResp.status };
  }
  return { ok: true, path };
}
