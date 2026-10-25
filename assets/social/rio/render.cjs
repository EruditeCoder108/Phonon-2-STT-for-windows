const path=require('path'),fs=require('fs'),{spawn}=require('child_process'),{once}=require('events');
const {serve}=require('./serve.cjs');
const bundle=process.env.CODEX_NODE_MODULES||'C:/Users/SAMBHAV/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const {chromium}=require(path.join(bundle,'playwright'));
const args=process.argv.slice(2),stills=args.includes('--stills');
const out=__dirname,WIDTH=args.includes('--4k')?3840:1920,HEIGHT=WIDTH*9/16,FPS=60,DURATION=24;
(async()=>{
 const server=await serve(),errors=[];let encoder;
 const browser=await chromium.launch({channel:'msedge',headless:true,args:['--hide-scrollbars','--disable-background-timer-throttling','--disable-renderer-backgrounding','--force-color-profile=srgb','--enable-webgl','--enable-unsafe-swiftshader']});
 try{
  const page=await browser.newPage({viewport:{width:WIDTH,height:HEIGHT},deviceScaleFactor:1});
  page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
  await page.goto(`http://127.0.0.1:${server.address().port}/?export=1`);await page.waitForFunction(()=>window.FILM_READY,null,{timeout:30000});
  fs.mkdirSync(path.join(out,'.render'),{recursive:true});
  for(const t of [1.5,3.2,6.5,7.4,8.6,11.6,13.5,16.7,18.6,21.7,23.3]){
   await page.evaluate(t=>renderFrame(t),t);await page.screenshot({path:path.join(out,'.render',`frame-${t.toFixed(1)}.png`)});
  }
  await page.evaluate(()=>renderFrame(13.5));await page.screenshot({path:path.join(out,'rio-landscape-cover.png')});
  const orbPage=await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
  await orbPage.goto(`http://127.0.0.1:${server.address().port}/?export=1`);await orbPage.waitForFunction(()=>window.FILM_READY);
  for(let variant=0;variant<3;variant++){
   await orbPage.evaluate(v=>window.renderOrb(3.2,v),variant);
   await orbPage.screenshot({path:path.join(out,'.render',`live-variant-${variant}.png`),clip:{x:420,y:0,width:1080,height:1080}});
  }
  await orbPage.close();
  if(errors.length)throw new Error(errors.join('\n'));
  console.log(`Rio storyboard ready: native procedural orb, ${WIDTH}×${HEIGHT}.`);
  if(stills)return;
  const target=path.join(out,WIDTH===3840?'rio-4k-video-only.mp4':'rio-1080-video-only.mp4');
  encoder=spawn(process.env.FFMPEG||'ffmpeg',['-hide_banner','-loglevel','error','-y','-f','image2pipe','-vcodec','mjpeg','-framerate',String(FPS),'-i','pipe:0','-an','-vf','scale=in_range=full:out_range=tv,format=yuv420p','-c:v','libx264','-preset','medium','-crf','16','-threads','6','-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709','-r',String(FPS),'-movflags','+faststart',target],{windowsHide:true,stdio:['pipe','ignore','pipe']});
  let log='';encoder.stderr.on('data',b=>log+=b.toString());
  const finished=new Promise((resolve,reject)=>{encoder.on('error',reject);encoder.on('close',c=>c===0?resolve():reject(new Error(log||`FFmpeg ${c}`)))});
  const started=Date.now();
  for(let frame=0;frame<FPS*DURATION;frame++){
   await page.evaluate(t=>renderFrame(t),frame/FPS);
   const image=await page.screenshot({type:'jpeg',quality:98,timeout:60000});
   if(!encoder.stdin.write(image))await once(encoder.stdin,'drain');
   if(frame%60===0)console.log(`Rio render ${frame}/${FPS*DURATION}; ${Math.round((Date.now()-started)/1000)} s elapsed`);
  }
  encoder.stdin.end();await finished;if(errors.length)throw new Error(errors.join('\n'));console.log('Video complete:',target);
 }finally{if(encoder&&!encoder.killed)encoder.kill();await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1});
