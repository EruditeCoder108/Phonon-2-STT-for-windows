/* Frame-accurate Chromium renderer, streamed to FFmpeg; no real-time recording. */
const path=require('path');const fs=require('fs');const {spawn}=require('child_process');const {once}=require('events');
const {serve}=require('./serve.cjs');
const bundle=process.env.CODEX_NODE_MODULES||'C:/Users/SAMBHAV/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const {chromium}=require(path.join(bundle,'playwright'));
const output=path.resolve(__dirname,'..');const fps=60,duration=24;
const ffmpeg=process.env.FFMPEG||'ffmpeg';
(async()=>{
  const server=await serve();const errors=[];
  const browser=await chromium.launch({channel:'msedge',headless:true,args:['--hide-scrollbars','--disable-background-timer-throttling','--disable-renderer-backgrounding','--force-color-profile=srgb']});
  let encoder;
  try{
    const page=await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
    page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(`http://127.0.0.1:${server.address().port}/?export=1`);
    await page.waitForFunction(()=>window.FILM_READY,{timeout:30000});
    const framesDir=path.join(__dirname,'.render');fs.mkdirSync(framesDir,{recursive:true});
    const timepoints=[.0,1.3,3.3,5.7,7.8,9.25,11.8,13.4,16.8,18.7,21.8,23.3];
    for(const t of timepoints){await page.evaluate(t=>window.renderFrame(t),t);await page.screenshot({path:path.join(framesDir,`frame-${t.toFixed(2)}.png`),animations:'allow'});}
    await page.evaluate(()=>window.renderFrame(3.3));await page.screenshot({path:path.join(output,'syllink-motion-cover.png')});
    // High-resolution reference for the poster revision, using the source orb.
    const orbPage=await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
    await orbPage.goto(`http://127.0.0.1:${server.address().port}/?export=1`);await orbPage.waitForFunction(()=>window.FILM_READY);
    await orbPage.evaluate(()=>{window.renderOrb(3.3,4.2);document.body.style.background='#060a0d';});
    await orbPage.screenshot({path:path.join(output,'syllink-actual-orb-reference.png'),clip:{x:570,y:150,width:780,height:780}});await orbPage.close();
    if(errors.length)throw new Error(errors.join('\n'));
    console.log('Storyboard and actual-orb reference rendered.');
    if(process.argv.includes('--stills'))return;
    const target=path.join(__dirname,'syllink_video-only.mp4');
    encoder=spawn(ffmpeg,['-hide_banner','-loglevel','warning','-y','-f','image2pipe','-vcodec','mjpeg','-framerate',String(fps),'-i','pipe:0','-an','-c:v','libx264','-preset','medium','-crf','17','-pix_fmt','yuv420p','-r',String(fps),'-movflags','+faststart',target],{windowsHide:true,stdio:['pipe','ignore','pipe']});
    let log='';encoder.stderr.on('data',b=>log+=b.toString());
    const completion=new Promise((resolve,reject)=>{encoder.on('error',reject);encoder.on('close',code=>code===0?resolve():reject(new Error(`FFmpeg ${code}: ${log}`)));});
    for(let frame=0;frame<fps*duration;frame++){
      await page.evaluate(t=>window.renderFrame(t),frame/fps);
      const image=await page.screenshot({type:'jpeg',quality:97,animations:'allow',timeout:30000});
      if(!encoder.stdin.write(image))await once(encoder.stdin,'drain');
      if(frame%120===0)console.log(`Rendered ${frame}/${fps*duration} frames (${(frame/fps).toFixed(1)} s)`);
    }
    encoder.stdin.end();await completion;
    if(errors.length)throw new Error(errors.join('\n'));
    console.log('Finished video frames:',target);
  }finally{if(encoder&&!encoder.killed)encoder.kill();await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
