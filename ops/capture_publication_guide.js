// Real deployed UCcloud screens, using an isolated verification account only.
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const auth = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const output = path.resolve('static/guide');
const base = 'https://comsv-03.tail5819db.ts.net';
(async()=>{
  fs.mkdirSync(output,{recursive:true});
  const browser = await chromium.launch({channel:'chrome',headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1360,height:900},deviceScaleFactor:1});
    const response = await context.request.post(base+'/api/auth/login',{headers:{Origin:base,Referer:base+'/'},data:{username:auth.username,password:auth.password}});
    assert.equal(response.status(),200,'Verification login failed');
    const page = await context.newPage();
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/',{waitUntil:'networkidle'});
    const card = page.locator('.instance-card').filter({has:page.locator(`a[data-terminal][href*="${auth.id}"]`)});
    await card.waitFor({state:'visible',timeout:60000});
    await card.screenshot({path:path.join(output,'pc-vm.png')});
    await page.goto(base+'/terminal?vm='+auth.id);
    await page.locator('#connect-form').waitFor({state:'visible',timeout:60000});
    // Password stays blank in the photographed login form.
    await page.locator('main').screenshot({path:path.join(output,'pc-connect.png')});
    await page.locator('#vm-password').fill(auth.vm_password);
    await page.locator('#connect').click();
    await page.locator('#file-panel').waitFor({state:'visible',timeout:150000});
    const demoFile = fs.existsSync(path.join(output,'pc-files.png')) ? 'hello.html' : 'index.html';
    await page.locator('#file-picker').setInputFiles({name:demoFile,mimeType:'text/html',buffer:Buffer.from('<h1>내 웹사이트</h1>')});
    await page.waitForFunction(name=>document.getElementById('file-feedback').textContent.includes('~/uploads/'+name),demoFile,{timeout:45000});
    await page.locator('#file-panel').screenshot({path:path.join(output,'pc-files.png')});
    await page.waitForFunction(()=>!document.getElementById('publish-start').disabled);
    assert.equal(await page.locator('#publish-result').isVisible(),true,'Live automatic publication status missing');
    // Exercise the actual authenticated WebSocket publication route as well.
    await page.locator('#publish-start').click();
    await page.waitForFunction(()=>!document.getElementById('publish-start').disabled,null,{timeout:180000});
    console.log('Live publication feedback: '+await page.locator('#publish-message').textContent());
    assert.match(await page.locator('#publish-message').textContent(),/완료/);
    assert.equal(await page.locator('#publish-result').isVisible(),true);
    assert.match(await page.locator('#publish-address').inputValue(),/^https:\/\/.*\.tail2d25b3\.ts\.net$/);
    await page.locator('#publish-panel').screenshot({path:path.join(output,'pc-publish.png')});
    await page.screenshot({path:'live-publication-pc.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('Real deployed PC screenshots, SSH/SFTP transfer and authenticated publication route verified.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error.name+': '+error.message);process.exitCode=1;});
