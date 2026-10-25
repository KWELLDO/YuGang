from __future__ import annotations

import hmac
import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .base import IncomingMessage


_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>鱼缸 · YuGang</title>
<style>
:root { color-scheme: dark; --bg:#08131a; --panel:#0e2029; --line:#1e3a45; --ink:#d8edf2; --dim:#7fa3ad; --accent:#6fd3e0; --warm:#f0b46a; }
* { box-sizing: border-box; }
body { margin:0; background:radial-gradient(1200px 600px at 50% -10%, #12313d 0%, var(--bg) 55%); color:var(--ink); font:16px/1.55 -apple-system,BlinkMacSystemFont,"Noto Sans SC","PingFang SC",sans-serif; min-height:100vh; }
main { max-width:760px; margin:0 auto; padding:20px 16px 40px; }
header { display:flex; align-items:baseline; justify-content:space-between; gap:12px; margin-bottom:14px; }
h1 { font-size:22px; margin:0; letter-spacing:.08em; font-weight:600; }
h1 small { color:var(--dim); font-size:12px; letter-spacing:.18em; margin-left:8px; font-weight:400; }
.pill { font-size:13px; padding:4px 10px; border-radius:999px; border:1px solid var(--line); color:var(--dim); white-space:nowrap; }
.pill.awake { color:#0b1a1f; background:var(--accent); border-color:var(--accent); }
.pill.paused { color:#ffd9a0; border-color:#8a5a22; background:#2a1c0d; }
.card { background:linear-gradient(180deg, rgba(18,42,52,.9), rgba(12,28,36,.9)); border:1px solid var(--line); border-radius:14px; padding:14px 15px; margin:12px 0; }
.card h2 { font-size:12px; letter-spacing:.16em; color:var(--dim); margin:0 0 8px; font-weight:500; text-transform:uppercase; }
.muted { color:var(--dim); font-size:14px; }
.item { padding:10px 0; border-top:1px solid var(--line); }
.item:first-child { border-top:0; padding-top:0; }
.claim { font-size:15px; line-height:1.55; }
.meta { color:var(--dim); font-size:12px; margin-top:4px; }
.falsifier { color:#a9c6cc; font-size:13px; margin-top:5px; }
.diff { color:var(--dim); font-size:12px; white-space:pre-wrap; margin:5px 0 0; }
.tag { display:inline-block; font-size:11px; border:1px solid var(--line); border-radius:999px; padding:1px 7px; margin-right:6px; color:var(--dim); }
.tag.open { color:var(--warm); border-color:#8a5a22; }
.status-line { font-size:15px; }
.big { font-size:18px; line-height:1.6; }
pre { white-space:pre-wrap; word-break:break-word; font:14px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace; margin:0; color:#c9e6eb; }
.chat { display:flex; flex-direction:column; gap:8px; max-height:44vh; overflow:auto; padding-right:2px; }
.bubble { border:1px solid var(--line); border-radius:12px; padding:8px 11px; max-width:88%; }
.bubble.me { align-self:flex-end; background:#16323d; border-color:#28505e; }
.bubble.her { align-self:flex-start; background:#0b1b22; }
.bubble .who { display:block; font-size:11px; color:var(--dim); margin-bottom:3px; letter-spacing:.08em; }
.bubble .text { white-space:pre-wrap; word-break:break-word; }
form { display:flex; gap:8px; margin-top:12px; }
textarea { flex:1; min-height:48px; max-height:140px; resize:vertical; background:#071217; color:var(--ink); border:1px solid var(--line); border-radius:10px; padding:10px 12px; font:inherit; }
button { background:var(--accent); color:#06151a; border:0; border-radius:10px; padding:0 18px; font-weight:600; font-size:15px; }
button:disabled { opacity:.45; }
.row { display:flex; gap:10px; flex-wrap:wrap; }
.row .card { flex:1 1 220px; }
details summary { cursor:pointer; color:var(--dim); font-size:13px; }
a { color:var(--accent); }
.foot { color:var(--dim); font-size:12px; margin-top:18px; text-align:center; }
</style>
</head>
<body>
<main>
<header>
  <h1>鱼缸<small>YUGANG</small></h1>
  <span id="pill" class="pill">连接中</span>
</header>

<section class="card">
  <h2>此刻</h2>
  <div id="now" class="big">正在读取她的状态…</div>
  <div id="sleep-note" class="muted" style="margin-top:6px"></div>
  <div id="alarm-note" class="muted" style="margin-top:4px"></div>
</section>

<section class="card">
  <h2>上一次的日记 <span id="diary-ts" class="muted"></span></h2>
  <pre id="diary">—</pre>
  <div id="next-intent" class="muted" style="margin-top:8px"></div>
</section>

<section class="card">
  <h2>说话</h2>
  <div id="chat" class="chat"></div>
  <form id="form">
    <textarea id="text" placeholder="写点什么。发出去会叫醒她。" maxlength="8000"></textarea>
    <button id="send" type="submit">发送</button>
  </form>
  <div id="send-note" class="muted" style="margin-top:6px">她睡着时发消息也会立刻醒来；醒来需要几秒到一分钟。</div>
</section>

<div class="row">
  <section class="card">
    <h2>她是谁</h2>
    <pre id="self">—</pre>
  </section>
  <section class="card">
    <h2>未闭合</h2>
    <div id="loops" class="muted">—</div>
  </section>
</div>

<section class="card">
  <h2>她现在的立场 <span id="stance-ts" class="muted"></span></h2>
  <div id="stance" class="muted">—</div>
</section>

<section class="card">
  <h2>她记得的片段 <span id="episode-count" class="muted"></span></h2>
  <div id="episodes"></div>
</section>

<section class="card">
  <h2>她怎么改过自己 <span id="self-history-count" class="muted"></span></h2>
  <div id="self-history"></div>
</section>

<section class="card">
  <h2>她相信什么 <span id="belief-count" class="muted"></span></h2>
  <div id="beliefs"></div>
</section>

<section class="card">
  <h2>还没验证的觉察 <span id="awareness-count" class="muted"></span></h2>
  <div id="awareness"></div>
</section>

<section class="card">
  <h2>她给自己定的做法 <span id="practice-count" class="muted"></span></h2>
  <div id="practices"></div>
</section>

<section class="card">
  <h2>最近发生</h2>
  <div id="events" class="muted">—</div>
</section>

<details class="card">
  <summary>预算与统计</summary>
  <pre id="stats" style="margin-top:10px">—</pre>
</details>

<div class="foot">身体在线，意识按需。私有实例，仅本机监听。</div>
</main>
<script>
const params = new URLSearchParams(location.search);
const incoming = params.get('token');
let token = incoming || sessionStorage.getItem('yg_token') || '';
let paused = false;
if (incoming) {
  sessionStorage.setItem('yg_token', incoming);
  history.replaceState({}, '', location.pathname);
}
const $ = (id) => document.getElementById(id);
function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (token) headers.Authorization = 'Bearer ' + token;
  const r = await fetch(path, Object.assign({}, opts, { headers }));
  if (r.status === 401) throw new Error('unauthorized');
  return r.json();
}
function fmt(ts) { if (!ts) return ''; const d = new Date(ts); return isNaN(d) ? ts : d.toLocaleString('zh-CN', { hour12:false }); }
function renderChat(events) {
  const box = $('chat'); box.replaceChildren();
  const rows = events.filter(e => ['message','reply','observation'].includes(e.kind)).slice(0, 14).reverse();
  if (!rows.length) { box.append(el('div','muted','还没有对话。')); return; }
  for (const e of rows) {
    const mine = e.kind === 'message';
    const b = el('div', 'bubble ' + (mine ? 'me' : 'her'));
    b.append(el('span','who', mine ? '你' : (e.actor || '她')));
    b.append(el('div','text', e.content || ''));
    box.append(b);
  }
  box.scrollTop = box.scrollHeight;
}
function renderLoops(loops) {
  const box = $('loops'); box.replaceChildren();
  if (!loops || !loops.length) { box.textContent = '没有未闭合的事。'; return; }
  for (const l of loops) box.append(el('div','', '· ' + l.title));
}
function tag(text, open) { return el('span', 'tag' + (open ? ' open' : ''), text); }
function renderBeliefs(judgments) {
  const box = $('beliefs'); box.replaceChildren();
  const rows = (judgments || []).filter(j => j.status !== 'refuted').slice(0, 6);
  $('belief-count').textContent = rows.length ? '最近 ' + rows.length + ' 条' : '';
  if (!rows.length) { box.append(el('div','muted','还没有带证据的判断。')); return; }
  for (const j of rows) {
    const d = el('div','item');
    d.append(el('div','claim', j.claim || ''));
    const meta = el('div','meta');
    const labels = {open:'待验证', supported:'已被支持', 'needs-evidence':'需更多证据', refuted:'已被推翻'};
    meta.append(tag(labels[j.status] || j.status || '待验证', j.status === 'open'));
    meta.append(document.createTextNode('置信 ' + (j.confidence ?? '?') + ' · 证据 ' + ((j.evidence_refs || []).length) + ' 条'));
    if ((j.supersedes || []).length) meta.append(tag('合并 ' + j.supersedes.length + ' 条旧判断', false));
    d.append(meta);
    if (j.falsifier) d.append(el('div','falsifier', '会被什么推翻：' + j.falsifier));
    box.append(d);
  }
}
function renderStance(s) {
  const box = $('stance'); box.replaceChildren();
  const note = $('stance-ts');
  if (!s || !s.content) { note.textContent = ''; box.textContent = '还没有形成明确的当前立场。'; return; }
  note.textContent = fmt(s.ts);
  const d = el('div','item');
  d.append(el('div','claim', s.content));
  const meta = el('div','meta');
  meta.append(document.createTextNode('依据 ' + ((s.based_on || []).length) + ' 条 L1 · ' + (s.trigger || '睡去时重算')));
  d.append(meta);
  box.append(d);
}
function renderEpisodes(episodes, count) {
  const box = $('episodes'); box.replaceChildren();
  const rows = (episodes || []).slice(0, 6);
  $('episode-count').textContent = count ? '共 ' + count + ' 段' : '';
  if (!rows.length) { box.append(el('div','muted','她还没有形成生活片段。')); return; }
  const origins = {model:'睡去时整理', diary:'日记索引', manual:'手动记录'};
  for (const ep of rows) {
    const d = el('div','item');
    d.append(el('div','claim', ep.title || '一次清醒'));
    const meta = el('div','meta');
    meta.append(document.createTextNode(fmt(ep.start_ts) + ' → ' + fmt(ep.end_ts) + ' · 依据 ' + (ep.event_count || (ep.event_ids || []).length) + ' 条 L1 · ' + (origins[ep.origin] || ep.origin || '')));
    d.append(meta);
    if (ep.summary) d.append(el('div','falsifier', String(ep.summary).slice(0,500)));
    box.append(d);
  }
}
function renderSelfHistory(versions) {
  const box = $('self-history'); box.replaceChildren();
  const rows = (versions || []).slice(0, 5);
  $('self-history-count').textContent = rows.length ? '最近 ' + rows.length + ' 次' : '';
  if (!rows.length) { box.append(el('div','muted','还没有修订记录。')); return; }
  for (const v of rows) {
    const d = el('div','item');
    d.append(el('div','claim', v.reason || v.id || ''));
    const meta = el('div','meta');
    meta.append(document.createTextNode(fmt(v.ts) + ' · 证据 ' + ((v.evidence_refs || []).length) + ' 条 · ' + (v.id || '')));
    d.append(meta);
    if (v.diff) d.append(el('pre','diff', String(v.diff).slice(0, 500)));
    box.append(d);
  }
}
function renderAwareness(cards) {
  const box = $('awareness'); box.replaceChildren();
  const rows = (cards || []).filter(c => c.status === 'open' || c.status === 'needs-evidence').slice(0, 6);
  $('awareness-count').textContent = rows.length ? rows.length + ' 张待复核' : '';
  if (!rows.length) { box.append(el('div','muted','目前没有还没验证的自我觉察。')); return; }
  for (const c of rows) {
    const d = el('div','item');
    d.append(el('div','claim', c.observation || c.hypothesis || ''));
    const meta = el('div','meta');
    meta.append(tag(c.status === 'needs-evidence' ? '需更多证据' : (c.trigger || '觉察'), c.status === 'open'));
    meta.append(document.createTextNode('置信 ' + (c.confidence ?? '?') + ' · 证据 ' + ((c.evidence_refs || []).length) + ' 条'));
    d.append(meta);
    if (c.falsifier) d.append(el('div','falsifier', '会被什么推翻：' + c.falsifier));
    box.append(d);
  }
}
function renderPractices(practices, stats) {
  const box = $('practices'); box.replaceChildren();
  const rows = (practices || []).slice(0, 8);
  const dropped = (stats && stats.dropped) || 0;
  const checks = (stats && stats.checks) || 0;
  $('practice-count').textContent = rows.length
    ? rows.length + ' 条仍有效' + (checks ? '，已检查 ' + checks + ' 次' : '') + (dropped ? '（已废弃 ' + dropped + ' 条）' : '')
    : '';
  if (!rows.length) { box.append(el('div','muted','她还没有给自己定过跨 session 的做法。')); return; }
  for (const p of rows) {
    const d = el('div','item');
    d.append(el('div','claim', p.text || ''));
    const meta = el('div','meta');
    meta.append(document.createTextNode('从 ' + fmt(p.ts) + ' 起 · 当初依据 ' + ((p.evidence_refs || []).length) + ' 条'));
    d.append(meta);
    if (p.last_check) {
      const labels = { followed: '做到了', violated: '没做到', untested: '今天没有相关情形' };
      const c = p.last_check;
      const cm = el('div','meta');
      cm.append(document.createTextNode('最近检查：' + (labels[c.status] || c.status) + ' · ' + fmt(c.ts) + (c.note ? ' · ' + String(c.note).slice(0,160) : '')));
      d.append(cm);
    }
    box.append(d);
  }
}
async function refresh() {
  try {
    const t = await api('/tank');
    const s = t.status || {};
    paused = !!s.paused;
    const pill = $('pill');
    pill.textContent = s.paused ? '已暂停' : (s.awake ? '醒着' : '睡着');
    pill.className = 'pill' + (s.paused ? ' paused' : (s.awake ? ' awake' : ''));
    const mins = s.awake ? Math.max(0, Math.round((s.wake_remaining_seconds || 0) / 60)) : 0;
    $('now').textContent = s.paused
      ? '操作员暂停了模型调用。消息仍会写进 L1，但不会叫醒她；恢复后她会看到。'
      : (s.awake
        ? '她醒着。这次清醒还剩约 ' + mins + ' 分钟，之后会自然睡去。'
        : '她睡着了。身体还在，闹钟和邮件轮询也在；你发消息会立刻叫醒她。');
    const ls = t.last_sleep;
    $('sleep-note').textContent = ls && ls.content ? '上一次睡去：' + fmt(ls.ts) + '。' + (ls.content.length > 160 ? ls.content.slice(0,160) + '…' : ls.content) : '还没有睡去记录。';
    const alarm = (t.alarms || [])[0];
    $('alarm-note').textContent = alarm ? '下一次自己设的醒来：' + fmt(alarm.due_at) : '';
    if (ls && ls.diary) {
      $('diary').textContent = ls.diary;
      $('diary-ts').textContent = ls.diary_ts ? fmt(ls.diary_ts) : '';
      $('next-intent').textContent = ls.next_intent ? '下次醒来想做：' + ls.next_intent : '';
    } else {
      $('diary').textContent = '她还没有写过睡去日记。';
      $('diary-ts').textContent = '';
      $('next-intent').textContent = '';
    }
    $('self').textContent = t.self || '（SELF.md 还没有内容）';
    renderStance(t.stance);
    renderEpisodes(t.episodes, t.episode_count);
    renderSelfHistory(t.self_history);
    renderLoops(t.open_loops);
    renderBeliefs(t.judgments);
    renderAwareness(t.awareness);
    renderPractices(t.practices, t.practice_stats);
    renderChat(t.recent_events || []);
    const ev = (t.recent_events || []).slice(0, 10).map(e => fmt(e.ts) + '  ' + e.kind + '  ' + String(e.content || '').slice(0,90));
    $('events').replaceChildren(...(ev.length ? ev.map(x => el('div','',x)) : [el('div','muted','还没有事件。')]));
    $('stats').textContent = JSON.stringify({ events: s.event_count, awake: s.awake, budget: s.budget, stage: s.budget_stage }, null, 2);
  } catch (err) {
    $('pill').textContent = err.message === 'unauthorized' ? '需要 token' : '断开';
    $('pill').className = 'pill';
    $('now').textContent = err.message === 'unauthorized'
      ? '这个页面需要访问 token。用带 ?token=… 的地址打开一次，之后会记住。'
      : '连不上网关。她可能正在重启。';
  }
}
$('form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const text = $('text').value.trim();
  if (!text) return;
  $('send').disabled = true; $('send').textContent = '…';
  $('send-note').textContent = paused ? '她被操作员暂停了；消息会写入 L1，恢复后再看。' : '她正在读。最多等三分钟。';
  try {
    const data = await api('/message', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ content:text, id:'web-' + Date.now() }) });
    if (data.reply) { $('text').value = ''; $('send-note').textContent = '已回复。'; }
    else { $('send-note').textContent = data.error || '这次没有回复，稍后再试。'; }
  } catch (err) {
    $('send-note').textContent = err.message === 'unauthorized' ? 'token 不对或已失效。' : '发送失败：' + err.message;
  } finally {
    $('send').disabled = false; $('send').textContent = '发送';
    refresh();
  }
});
refresh();
setInterval(refresh, 8000);
</script>
</body>
</html>
"""

_TOKEN_PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>鱼缸</title><body style="background:#08131a;color:#d8edf2;font-family:sans-serif;padding:2rem"><h1>需要 token</h1><p>用 <code>/?token=…</code> 打开这个页面。</p></body>"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "YuGangGateway/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the gateway quiet; errors still go through send_json.
        return

    @property
    def channel(self) -> "HttpChannel":
        return self.server.channel  # type: ignore[attr-defined]

    def _json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, *, query_token: str | None = None) -> bool:
        token = self.channel.token
        if not token:
            return True
        if query_token is not None and hmac.compare_digest(query_token, token):
            return True
        header = self.headers.get("Authorization", "")
        return hmac.compare_digest(header, f"Bearer {token}")

    def _html(self, status: int, page: str) -> None:
        body = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/health":
            health = self.channel.service.health()
            self._json(200 if health.get("ok") else 503, health)
            return
        if path in ("/", "/index.html"):
            token = (parse_qs(parts.query).get("token") or [None])[0]
            if not self._authorized(query_token=token):
                self._html(401, _TOKEN_PAGE)
                return
            self._html(200, _PAGE)
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if path == "/status":
            self._json(200, self.channel.service.status())
            return
        if path == "/tank":
            self._json(200, self.channel.service.tank())
            return
        if path == "/audit":
            try:
                report = self.channel.service.audit()
            except Exception as exc:  # noqa: BLE001 - report, never crash the gateway
                self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                return
            self._json(200 if report.get("ok") else 409, report)
            return
        if path == "/replies":
            self._json(200, {"replies": list(self.channel.replies)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in ("/message", "/sleep", "/pause", "/resume"):
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw or "{}")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "invalid JSON"})
            return
        if path == "/pause":
            result = self.channel.service.pause(reason=str(payload.get("reason") or "operator"))
            self._json(200, {"ok": True, **result})
            return
        if path == "/resume":
            result = self.channel.service.resume()
            self._json(200, {"ok": True, **result})
            return
        if path == "/sleep":
            if self.channel.service.is_paused():
                self._json(409, {"error": "paused; resume first"})
                return
            if not self.channel.service.window.is_awake():
                self._json(409, {"error": "not awake"})
                return
            applied = self.channel.service.sleep_window(reason=str(payload.get("reason") or "operator"))
            self._json(200 if applied else 503, {"ok": bool(applied), "sleep": applied})
            return
        content = str(payload.get("content") or "").strip()
        if not content:
            self._json(400, {"error": "content is required"})
            return
        if len(content) > 8000:
            self._json(413, {"error": "content too long"})
            return
        incoming = IncomingMessage(
            channel="http",
            content=content,
            sender=str(payload.get("sender") or "web")[:200],
            external_id=str(payload.get("id"))[:200] if payload.get("id") else None,
            wait_for_reply=True,
        )
        handled = self.channel.on_message(incoming)
        if handled is not None:
            incoming.message_id = getattr(handled, "id", None)
        ok = incoming.wait()
        self._json(
            200 if ok and not incoming.error else 503,
            {
                "message_id": incoming.message_id,
                "reply": incoming.reply,
                "error": incoming.error,
                "timed_out": not ok,
            },
        )


class HttpChannel:
    name = "http"

    def __init__(self, host: str, port: int, *, token: str | None = None):
        self.host = host
        self.port = port
        self.token = token
        self.on_message: Callable[[IncomingMessage], Any] | None = None
        self.service: Any = None
        self.replies: deque[dict[str, Any]] = deque(maxlen=50)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def bind(self, service: Any) -> None:
        self.service = service

    def start(self, on_message: Callable[[IncomingMessage], Any]) -> None:
        self.on_message = on_message
        server = ThreadingHTTPServer((self.host, self.port), _Handler)
        server.daemon_threads = True
        server.channel = self  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="yugang-http", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def send(self, recipient: str | None, content: str, *, meta: dict[str, Any] | None = None) -> bool:
        self.replies.append({"to": recipient, "content": content, "meta": meta or {}})
        return True

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)