// 发布带正文插图的头条图文文章
// 正文文件用 [[IMG:图片绝对路径]] 标记插图位置
// 用法: node publish-with-images.mjs --title "标题" --content-file a.txt [--cover c.png] [--dry-run]
import { launchBrowser, closeBrowser, sleep, waitForStable, dismissOverlays }
  from '../../../mcp-service/servers/urara/toutiao-ops-cli/node_modules/@openclaw-cn/toutiao-ops/src/browser.js';
import { ensureLoggedIn }
  from '../../../mcp-service/servers/urara/toutiao-ops-cli/node_modules/@openclaw-cn/toutiao-ops/src/auth-guard.js';
import { readFileSync, writeFileSync } from 'fs';

const arg = (k, d) => {
  const i = process.argv.indexOf('--' + k);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[i + 1] : d;
};
const flag = (k) => process.argv.includes('--' + k);

const TITLE = arg('title');
const CONTENT_FILE = arg('content-file');
const COVER = arg('cover');
const DRY = flag('dry-run');
const OUT = arg('out', process.cwd());

if (!TITLE || !CONTENT_FILE) {
  console.error('用法: node publish-with-images.mjs --title "标题" --content-file a.txt [--cover c.png] [--dry-run]');
  process.exit(2);
}

// ── 解析正文：按 [[IMG:path]] 切成文本块与图片块 ──
function parseContent(file) {
  const raw = readFileSync(file, 'utf-8');
  const blocks = [];
  const re = /\[\[IMG:([^\]]+)\]\]/g;
  let last = 0, m;
  while ((m = re.exec(raw))) {
    const txt = raw.slice(last, m.index).trim();
    if (txt) blocks.push({ type: 'text', v: txt });
    blocks.push({ type: 'img', v: m[1].trim() });
    last = m.index + m[0].length;
  }
  const tail = raw.slice(last).trim();
  if (tail) blocks.push({ type: 'text', v: tail });
  return blocks;
}

const report = { ok: false, title: TITLE, dryRun: DRY, blocks: 0, steps: [], resps: [] };
const log = (m) => report.steps.push(m);

const { context, page } = await launchBrowser({ headless: true });
page.on('response', async (res) => {
  const u = res.url();
  if (/\/article\/(publish|save)/i.test(u)) {
    let b = '';
    try { b = (await res.text()).slice(0, 300); } catch {}
    report.resps.push({ status: res.status(), body: b });
  }
});

const edStat = async () => await page.evaluate(() => {
  const ed = document.querySelector('.ProseMirror');
  if (!ed) return { uniqSrc: 0 };
  const srcs = [...new Set([...ed.querySelectorAll('img')].map((i) => i.getAttribute('src') || ''))];
  return { tagCount: ed.querySelectorAll('img').length, uniqSrc: srcs.length };
});

try {
  const blocks = parseContent(CONTENT_FILE);
  report.blocks = blocks.length;
  report.imgBlocks = blocks.filter((b) => b.type === 'img').length;
  log('blocks=' + blocks.length + ' imgs=' + report.imgBlocks);

  await ensureLoggedIn(page);
  await page.goto('https://mp.toutiao.com/profile_v4/graphic/publish', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await waitForStable(page);
  await sleep(1600, 2400);
  await dismissOverlays(page);
  await sleep(500, 900);

  // ── 标题 ──
  const t = TITLE.length > 30 ? TITLE.slice(0, 30) : TITLE;
  const TSEL = 'textarea[placeholder*="标题"]:visible';
  await page.waitForSelector(TSEL, { timeout: 15000 });
  await page.locator(TSEL).first().fill(t);
  log('title=' + (await page.locator(TSEL).first().inputValue()));

  // ── 正文（逐块录入，遇图片块走素材抽屉） ──
  const ED = '.ProseMirror';
  await page.click(ED, { force: true });
  await sleep(300, 500);

  let idx = 0;
  for (const b of blocks) {
    idx++;
    if (b.type === 'text') {
      await page.keyboard.type(b.v, { delay: 12 });
      await page.keyboard.press('Enter');
      await sleep(300, 500);
      log('text[' + idx + '] len=' + b.v.length);
    } else {
      // ① 开抽屉  ② 喂文件  ③ 点确定
      await page.locator('.syl-toolbar-tool.image').first().click({ force: true, timeout: 8000 }).catch((e) => log('img btn err: ' + String(e.message).slice(0, 60)));
      await sleep(1500, 2200);
      const hasDrawer = await page.evaluate(() => !!document.querySelector('.byte-drawer-wrapper'));
      if (!hasDrawer) { log('img[' + idx + '] NO drawer, skip'); continue; }
      await page.locator('.byte-drawer-wrapper input[type=file]').first().setInputFiles(b.v).catch((e) => log('img feed err: ' + String(e.message).slice(0, 60)));
      await sleep(5000, 6500);
      await page.locator('.byte-drawer-wrapper button:has-text("确定")').first().click({ timeout: 8000 }).catch((e) => log('img confirm err: ' + String(e.message).slice(0, 60)));
      await sleep(2500, 3500);
      const st = await edStat();
      log('img[' + idx + '] ' + b.v.split(/[\\/]/).pop() + ' -> editor uniqSrc=' + st.uniqSrc);
    }
  }
  report.afterContent = await edStat();
  log('after content: uniqSrc=' + report.afterContent.uniqSrc);
  await page.screenshot({ path: OUT + '/pub-after-content.png' });

  // ── 封面（单图模式，头条自动取正文首图；这里只确保模式选中） ──
  const singleCnt = await page.locator('text=单图').count();
  if (singleCnt > 0) {
    await page.locator('text=单图').first().click({ timeout: 5000 }).catch(() => {});
    await sleep(600, 900);
    log('cover mode: 单图 selected (auto from first inline image)');
  }

  // ── 预览并发布 ──
  await dismissOverlays(page);
  const pubBtn = page.locator('button:has-text("预览并发布")').first();
  const pbCnt = await page.locator('button:has-text("预览并发布")').count();
  log('预览并发布 count=' + pbCnt);
  if (pbCnt === 0) throw new Error('未找到「预览并发布」按钮');
  await pubBtn.scrollIntoViewIfNeeded().catch(() => {});
  await sleep(300, 500);
  await pubBtn.click({ force: true, timeout: 10000 });
  await sleep(4000, 5000);
  await waitForStable(page);

  const confirmCnt = await page.locator('button:has-text("确认发布")').count();
  report.confirmBtnCount = confirmCnt;
  log('确认发布 count=' + confirmCnt);
  await page.screenshot({ path: OUT + '/pub-before-confirm.png' });

  if (DRY) {
    log('DRY-RUN: stopped before 确认发布 (not published)');
    report.action = 'dry_run_stopped_at_confirm';
  } else if (confirmCnt > 0) {
    await page.locator('button:has-text("确认发布")').first().click({ timeout: 10000 });
    await sleep(4000, 6000);
    await waitForStable(page);
    report.finalUrl = page.url();
    report.finalText = await page.evaluate(() => (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 800));
    await page.screenshot({ path: OUT + '/pub-after-confirm.png' });
    log('CONFIRMED publish. url=' + report.finalUrl);
    report.action = 'published';
  }
  report.ok = true;
} catch (e) {
  report.error = String((e && e.message) || e);
  log('ERROR: ' + report.error);
  try { await page.screenshot({ path: OUT + '/pub-err.png' }); } catch {}
} finally {
  try { await closeBrowser(context); } catch {}
  writeFileSync(OUT + '/publish-images-report.json', JSON.stringify(report, null, 2), 'utf-8');
  console.log('DONE ok=' + report.ok + ' action=' + (report.action || '-') + ' imgs=' + report.imgBlocks
    + ' editorSrc=' + ((report.afterContent && report.afterContent.uniqSrc) || 0)
    + ' confirmBtns=' + report.confirmBtnCount);
  (report.resps || []).forEach((r) => console.log('  RESP ' + r.status + ' ' + String(r.body).replace(/\s+/g, ' ').slice(0, 150)));
}
