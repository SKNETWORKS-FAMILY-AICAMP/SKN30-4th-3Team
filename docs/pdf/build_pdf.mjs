// Farm하니 v2 제출 문서 → PDF 변환 스크립트
// 사용법:
//   1) Node.js 18+ 설치, Chrome 또는 Edge 설치 (헤드리스 렌더링에 사용)
//   2) docs/pdf 폴더에서:  npm install markdown-it puppeteer-core
//   3) node build_pdf.mjs
// 결과: docs/pdf/*.pdf (A4 가로, 한글 폰트, 표지·페이지 번호 포함)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import MarkdownIt from 'markdown-it';
import puppeteer from 'puppeteer-core';

const HERE = path.dirname(fileURLToPath(import.meta.url)); // docs/pdf
const DOCS = path.dirname(HERE);                            // docs
const OUT = HERE;

const BROWSERS = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].filter(Boolean);
const CHROME = BROWSERS.find((p) => fs.existsSync(p));
if (!CHROME) { console.error('Chrome/Edge를 찾을 수 없습니다. CHROME_PATH 환경변수로 지정하세요.'); process.exit(1); }

const DOCLIST = [
  { file: 'Farm하니_v2_요구사항_정의서.md',        name: 'Farm하니 v2 요구사항 정의서',        h2break: true  },
  { file: 'Farm하니_v2_화면설계서.md',             name: 'Farm하니 v2 화면설계서',             h2break: false },
  { file: 'Farm하니_v2_시스템_구성도.md',           name: 'Farm하니 v2 시스템 구성도',           h2break: false },
  { file: 'Farm하니_v2_통합테스트_계획서.md',       name: 'Farm하니 v2 통합테스트 계획서',       h2break: true  },
  { file: 'Farm하니_v2_통합테스트_결과보고서.md',   name: 'Farm하니 v2 통합테스트 결과보고서',   h2break: true  },
];

const md = new MarkdownIt({ html: true, linkify: true, typographer: false, breaks: false });

const baseCss = `
  @page { size: A4 landscape; margin: 14mm 13mm; }
  * { box-sizing: border-box; }
  html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  body { font-family: 'Pretendard','Noto Sans KR','Malgun Gothic','Apple SD Gothic Neo',sans-serif;
    color: #1a2b2f; font-size: 10pt; line-height: 1.62; word-break: keep-all; margin: 0; }
  h1 { font-size: 23pt; font-weight: 800; letter-spacing: -.025em; color: #0a3f2e; margin: 4px 0 16px; text-wrap: balance; }
  h2 { font-size: 15pt; font-weight: 800; color: #0a5038; margin: 22px 0 11px; padding-bottom: 6px;
    border-bottom: 2px solid #12b5a0; break-after: avoid; }
  h3 { font-size: 12.5pt; font-weight: 700; color: #0b8073; margin: 16px 0 8px; break-after: avoid; }
  h4 { font-size: 11pt; font-weight: 700; color: #1a2b2f; margin: 12px 0 6px; break-after: avoid; }
  p { margin: 6px 0; }
  a { color: #1f6fa8; text-decoration: none; word-break: break-all; }
  strong { color: #0a3f2e; }
  code { font-family: 'Cascadia Code',Consolas,monospace; font-size: .85em; background: #e9f4f2;
    color: #0b6b5e; padding: 1px 5px; border-radius: 4px; overflow-wrap: anywhere; }
  pre { background: #0c2b33; color: #e8f4f2; padding: 12px 15px; border-radius: 8px; margin: 10px 0;
    font-size: 8.5pt; line-height: 1.55; break-inside: avoid; white-space: pre-wrap; overflow-wrap: anywhere; }
  pre code { background: none; color: inherit; padding: 0; }
  table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 8.7pt; break-inside: avoid; }
  th, td { border: 1px solid #d5e2df; padding: 5px 8px; text-align: left; vertical-align: top; line-height: 1.45; }
  th { background: #e2f2ed; color: #0a5038; font-weight: 700; }
  tbody tr:nth-child(even) td { background: #f6fbfa; }
  img { max-width: 100%; max-height: 142mm; height: auto; width: auto; display: block; margin: 12px auto; break-inside: avoid; }
  em { color: #5b6b70; font-style: italic; }
  blockquote { margin: 11px 0; padding: 9px 15px; border-left: 4px solid #12b5a0; background: #f2f9f8;
    color: #45585d; break-inside: avoid; }
  blockquote p { margin: 3px 0; }
  ul, ol { margin: 6px 0 6px 22px; }
  li { margin: 3px 0; }
  hr { border: none; border-top: 1px solid #d5e2df; margin: 18px 0; }
  .pagebreak { break-before: page; height: 0; }
`;

const tpl = (name, body, h2break) =>
  `<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>${name}</title>
<style>${baseCss}${h2break ? 'h2{break-before:page;}' : ''}</style></head><body>${body}</body></html>`;

function insertCoverBreak(html) {
  const i = html.indexOf('</table>');
  if (i === -1) return html;
  const p = i + 8;
  return html.slice(0, p) + '\n<div class="pagebreak"></div>\n' + html.slice(p);
}

const footer = `<div style="font-size:8px;width:100%;padding:0 13mm;color:#8a9a9a;font-family:'Malgun Gothic',sans-serif;">
  <span style="float:left;">Farm하니 v2 · SKN-30기 3팀</span>
  <span style="float:right;"><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>`;
const headerFor = (name) => `<div style="font-size:8px;width:100%;padding:0 13mm;color:#a9b6b6;font-family:'Malgun Gothic',sans-serif;">
  <span style="float:right;">${name} · v1.0</span></div>`;

const browser = await puppeteer.launch({ executablePath: CHROME, headless: 'new',
  args: ['--no-sandbox', '--allow-file-access-from-files', '--disable-gpu'] });

for (const d of DOCLIST) {
  const raw = fs.readFileSync(path.join(DOCS, d.file), 'utf8');
  let body = md.render(raw);
  // h2 대단원 제목 바로 뒤에 붙은 중복 페이지나눔 div 제거 (제목만 남는 빈 페이지 방지)
  body = body.replace(/(<\/h2>\s*)<div[^>]*page-break-before[^>]*>\s*<\/div>/gi, '$1');
  if (!d.h2break) body = insertCoverBreak(body);
  const tmp = path.join(DOCS, '.__pdfbuild_' + d.file.replace(/\.md$/, '') + '.html');
  fs.writeFileSync(tmp, tpl(d.name, body, d.h2break), 'utf8');

  const page = await browser.newPage();
  await page.goto('file:///' + tmp.replace(/\\/g, '/'), { waitUntil: 'networkidle0', timeout: 60000 });
  await page.pdf({
    path: path.join(OUT, d.file.replace(/\.md$/, '.pdf')),
    format: 'A4', landscape: true, printBackground: true, displayHeaderFooter: true,
    headerTemplate: headerFor(d.name), footerTemplate: footer,
    margin: { top: '15mm', bottom: '14mm', left: '13mm', right: '13mm' },
  });
  await page.close();
  fs.rmSync(tmp, { force: true });
  console.log('OK ', d.file.replace(/\.md$/, '.pdf'));
}
await browser.close();
console.log('완료 →', OUT);
