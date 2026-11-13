const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const dom = new JSDOM(fs.readFileSync('dist/index.html','utf8'),{url:'https://example.test',runScripts:'outside-only'});
const w = dom.window;
w.scrollTo=()=>{};
w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
w.HTMLDialogElement.prototype.close=function(){this.open=false;};
w.eval(fs.readFileSync('dist/snapshot.js','utf8'));
// Remote issue titles must stay inert when rendered.
w.PROJECT_SNAPSHOT.tasks[0].title='<img src=x onerror=alert(1)>';
w.eval(fs.readFileSync('dist/app.js','utf8'));
const doc=w.document;
assert.match(doc.querySelector('h1').textContent,/clear view/);
assert.equal(doc.querySelectorAll('.stat').length,4);
assert.equal(doc.querySelectorAll('.road-row').length,6);
function go(hash){w.location.hash=hash;w.dispatchEvent(new w.HashChangeEvent('hashchange'));}
go('tasks');
assert.equal(doc.querySelectorAll('tbody tr').length,w.PROJECT_SNAPSHOT.tasks.length);
assert.equal(doc.querySelectorAll('img').length,0);
const search=doc.querySelector('input[type=search]');
search.value='earnings';search.dispatchEvent(new w.Event('input'));
assert.equal(doc.querySelectorAll('tbody tr').length,1);
assert.match(doc.querySelector('tbody').textContent,/earnings/);
search.value='';search.dispatchEvent(new w.Event('input'));
const phase=doc.querySelector('[aria-label="Filter by phase"]');phase.value='1';phase.dispatchEvent(new w.Event('change'));
assert.equal(doc.querySelectorAll('tbody tr').length,w.PROJECT_SNAPSHOT.tasks.filter(t=>t.phase===1).length);
doc.querySelector('tbody button').click();assert.equal(doc.querySelector('dialog').open,true);
doc.querySelector('#close-dialog').click();assert.equal(doc.querySelector('dialog').open,false);
go('reviews');assert.equal(doc.querySelectorAll('.review-card').length,w.PROJECT_SNAPSHOT.prs.filter(p=>p.state==='OPEN').length);
go('roadmap');assert.equal(doc.querySelectorAll('.phase-card').length,6);
go('setup');const checkbox=doc.querySelector('input[type=checkbox]');checkbox.checked=true;checkbox.dispatchEvent(new w.Event('change'));
assert.equal(JSON.parse(w.localStorage.getItem('semi-edge-owner-checks')).paper,true);
go('setup');assert.equal(doc.querySelector('input[type=checkbox]').checked,true);
doc.querySelector('#refresh-info').click();assert.equal(doc.querySelector('dialog').open,true);
assert.match(doc.querySelector('#detail-body').textContent,/saved snapshot/);
assert.equal([...doc.querySelectorAll('a[href^="#"]')].filter(a=>a.target==='_blank').length,0);
console.log('PASS: overview, roadmap, search, phase filter, task details, reviews, persisted checklist, snapshot help, safe rendering, internal navigation');
w.close();

function boot(change=()=>{}, stored){
  const d=new JSDOM(fs.readFileSync('dist/index.html','utf8'),{url:'https://example.test',runScripts:'outside-only'});
  d.window.scrollTo=()=>{};
  d.window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  d.window.HTMLDialogElement.prototype.close=function(){this.open=false;};
  d.window.eval(fs.readFileSync('dist/snapshot.js','utf8'));
  change(d.window.PROJECT_SNAPSHOT);
  if(stored!==undefined)d.window.localStorage.setItem('semi-edge-owner-checks',stored);
  d.window.eval(fs.readFileSync('dist/app.js','utf8'));
  return d.window;
}
function route(window, page){window.location.hash=page;window.dispatchEvent(new window.HashChangeEvent('hashchange'));}
{
  const window=boot(data=>data.tasks.filter(t=>t.owner==='human').forEach(t=>t.status='DONE'));
  assert.equal(window.document.querySelector('.stat:last-child .stat-value').textContent,'0');
  assert.match(window.document.querySelector('.callout').textContent,/No blocked owner items/);
  window.close();
}
{
  const window=boot(data=>data.tasks.filter(t=>t.phase===1).forEach(t=>t.status='DONE'));
  assert.equal(window.document.querySelector('.hero-number').textContent,'02');
  assert.match(window.document.querySelector('.hero-title').textContent,/Backtesting/);
  window.close();
}
{
  const window=boot(data=>{data.review_queue=[{number:45,reviewer:'claude',verdict:'FAIL: fix outstanding inputs'}];data.prs=[{number:45,title:'Ingestion',state:'OPEN',url:'javascript:alert(document.domain)',headRefName:'feature/42-offline-ingestion',headRefOid:'abcdefg'}];});
  route(window,'reviews');
  assert.match(window.document.querySelector('.review-card').textContent,/FAIL: fix outstanding inputs/);
  assert.equal(window.document.querySelectorAll('a[href^="javascript:"]').length,0);
  window.close();
}
{
  const window=boot(data=>{data.tasks=[{id:'T42',issue:'#42',title:'Fixture importer',owner:'codex',status:'REVIEW',raw:'REVIEW (PR #45)',dependencies:'',phase:1}];data.prs=[{number:4,state:'OPEN',headRefName:'feature/4-other',url:'https://github.com/Ferrsir/semi-edge/pull/4'},{number:45,state:'OPEN',headRefName:'feature/42-offline-ingestion',url:'https://github.com/Ferrsir/semi-edge/pull/45'}];});
  route(window,'tasks');
  assert.equal([...window.document.querySelectorAll('tbody a')].filter(a=>a.textContent==='PR #4').length,0);
  assert.equal([...window.document.querySelectorAll('tbody a')].filter(a=>a.textContent==='PR #45').length,1);
  window.close();
}
for(const malformed of ['null','[]','"bad"','0','{"paper":"yes"}']){
  const window=boot(()=>{},malformed);route(window,'setup');
  assert.equal(window.document.querySelectorAll('.setup-card').length,4);
  assert.equal(window.document.querySelector('input[type=checkbox]').checked,false);
  window.close();
}
console.log('PASS: dynamic owner count, phase advancement, source review verdicts, URL allowlist, exact PR links, malformed browser state');
