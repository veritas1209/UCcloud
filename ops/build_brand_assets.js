/* Render the checked-in SVG sources, without external fonts or image services. */
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const brand = path.resolve(__dirname, '../static/brand');

(async () => {
  const browser = await chromium.launch({channel: 'chrome', headless: true});
  try {
    const page = await browser.newPage({deviceScaleFactor: 1});
    const icon = fs.readFileSync(path.join(brand, 'icon.svg'), 'utf8');
    const icoImages = [];
    for (const size of [16, 32, 48, 180, 192, 512]) {
      await page.setViewportSize({width: size, height: size});
      await page.setContent(`<html><head><style>html,body{margin:0;background:white}svg{display:block;width:100vw;height:100vh}</style></head><body>${icon}</body></html>`);
      const png = await page.screenshot({type: 'png'});
      if (size <= 48) icoImages.push({size, png});
      else fs.writeFileSync(path.join(brand, `icon-${size}-v1.png`), png);
    }
    // ICO directory with PNG entries: lossless 16, 32 and 48 pixel fallbacks.
    const header = Buffer.alloc(6 + 16 * icoImages.length);
    header.writeUInt16LE(1, 2);
    header.writeUInt16LE(icoImages.length, 4);
    let offset = header.length;
    icoImages.forEach(({size, png}, index) => {
      const entry = 6 + 16 * index;
      header[entry] = header[entry + 1] = size;
      header.writeUInt16LE(1, entry + 4);
      header.writeUInt16LE(32, entry + 6);
      header.writeUInt32LE(png.length, entry + 8);
      header.writeUInt32LE(offset, entry + 12);
      offset += png.length;
    });
    fs.writeFileSync(path.join(brand, 'favicon-v1.ico'), Buffer.concat([header, ...icoImages.map(i => i.png)]));
    await page.setViewportSize({width: 1200, height: 630});
    await page.goto(pathToFileURL(path.join(brand, 'share.svg')).href);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({path: path.join(brand, 'share-v1.png'), type: 'png'});
    console.log('Built UCcloud favicon, home-screen icons and 1200×630 sharing image.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
