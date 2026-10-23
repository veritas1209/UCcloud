const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const base='https://comsv-03.tail5819db.ts.net';
const json=(route,value)=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
  for(const width of [1360,390]) {
   const page=await browser.newPage({viewport:{width,height:900}}); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
   let posted;
   await page.route('**/*',route=>{
    const url=new URL(route.request().url()); const files={'/':'index.html','/assets/app.js':'app.js','/assets/styles.css':'styles.css'};
    if(files[url.pathname] && process.env.UI_USE_LIVE_STATIC!=='1') return route.fulfill({path:path.resolve('static',files[url.pathname])});
    if(url.pathname==='/api/me') return json(route,{username:'student',status:'active',role:'member',team_name:'test',team_slug:'test',csrf_token:'test'});
    if(url.pathname==='/api/public-config') return json(route,{});
    if(url.pathname==='/api/instances') {
     if(route.request().method()==='POST') {posted=route.request().postDataJSON(); return json(route,{});}
     return json(route,{instances:[]});
    }
    if(url.pathname==='/api/quota') return json(route,{instances:{limit:4,in_use:0},cores:{limit:8,in_use:0},ram:{limit:16384,in_use:0}});
    if(url.pathname==='/api/catalog') return json(route,{images:[{id:'ubuntu',name:'ubuntu-24.04'}],flavors:[{id:'large',name:'large',ram:8192,vcpus:4,disk:40}],software:[{id:'mysql',name:'MySQL',group:'데이터베이스',note:'DB',versions:['8.4'],port:3306,min_ram:4096,credentials:true},{id:'nginx',name:'Nginx',group:'웹서버',note:'웹서버',versions:['1.30'],port:8082,min_ram:2048}]});
    if(url.pathname.startsWith('/api/')) return json(route,{});
    return route.continue();
   });
   await page.goto(base); await page.locator('#open-create').click();
   await page.locator('#software-select option[value=mysql]').waitFor({state:'attached'});
   await page.locator('#software-select').selectOption('mysql');
   await page.locator('[data-software-username=mysql]').fill('dbuser');
   await page.locator('[data-software-password=mysql]').fill("simple$'\\pass");
   await page.locator('[data-service-password-toggle=mysql]').click();
   assert.equal(await page.locator('[data-software-password=mysql]').getAttribute('type'),'text');
   await page.locator('[data-service-password-toggle=mysql]').click();
   await page.locator('#software-select').selectOption('nginx');
   assert.equal(await page.locator('[data-software-password=mysql]').inputValue(),"simple$'\\pass");
   assert.equal(await page.locator('[data-software-username=mysql]').inputValue(),'dbuser');
   assert.equal(await page.locator('[data-software-password=nginx]').count(),0);
   assert.equal(await page.locator('[data-software-password=mysql]').getAttribute('type'),'password');
   const serialized=await page.locator('#software-list').innerHTML(); assert.ok(!serialized.includes("simple$"));
   await page.locator('#create-dialog').screenshot({path:'service-credentials-'+width+'.png'});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   await page.locator('#create-form input[name=vm_password]').fill('vmtesting');
   await page.locator('#create-form button[type=submit]:not([disabled])').waitFor();
   await page.locator('#create-form button[type=submit]').click();
   await page.waitForFunction(()=>!document.querySelector('#create-dialog').open);
   assert.equal(posted.software[0].username,'dbuser');
   assert.equal(posted.software[0].password,"simple$'\\pass");
   await page.waitForFunction(()=>document.querySelectorAll('[data-software-password]').length===0);
   assert.equal(await page.locator('[data-software-password]').count(),0);
   assert.deepEqual(errors,[]); await page.close();
  }
  console.log('PC/mobile service credentials, show/hide, retained values, no HTML password and close cleanup passed');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
