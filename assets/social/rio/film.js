/* Deterministic 60 fps choreography. Motion's actual spring generator drives
   entrances; the app's CSS mask is sampled at exact animation times. */
(async function () {
  const $ = id => document.getElementById(id);
  const clamp = x => Math.max(0, Math.min(1, x));
  const mix = (a, b, x) => a + (b - a) * x;
  const ease = x => { x = clamp(x); return x*x*x*(x*(x*6-15)+10); };
  const springs = new Map();
  function spring(t, duration=.7, bounce=.08) {
    if(t <= 0) return 0;
    if(t >= duration*4) return 1;
    const key = `${duration}/${bounce}`;
    if(!springs.has(key)) springs.set(key, Motion.spring({keyframes:[0,1],visualDuration:duration,bounce}));
    return springs.get(key).next(t*1000).value;
  }
  const fade = (t, start, duration=.45) => ease((t-start)/duration);
  function reveal(id,t,start,{duration=.75,y=70,blur=5,bounce=.04,scale=1}={}) {
    const element=$(id), p=spring(t-start,duration,bounce), a=fade(t,start,.35);
    element.style.opacity = a;
    element.style.transform=`translate3d(0,${(1-p)*y}px,0) scale(${mix(scale,1,p)})`;
    element.style.filter=`blur(${(1-ease((t-start)/duration))*blur}px)`;
  }
  function scene(id,t,start,end) {
    const alpha=fade(t,start,.35)*(1-fade(t,end,.42));
    const el=$(id);
    el.style.opacity=alpha;
    el.style.visibility=alpha>.0001?'visible':'hidden';
    const out=fade(t,end,.7);
    el.style.transform=`translate3d(0,${-42*out}px,0) scale(${1-.012*out})`;
  }
  function path(t,stops) {
    for(let i=1;i<stops.length;i++) if(t<stops[i][0]) {
      const [a,x,y,s]=stops[i-1], [b,xx,yy,ss]=stops[i];
      const p=ease((t-a)/(b-a));
      return [mix(x,xx,p),mix(y,yy,p),mix(s,ss,p)];
    }
    return stops.at(-1).slice(1);
  }
  function resize() {
    const scale=Math.min(innerWidth/1920,innerHeight/1080);
    $('stage').style.transform=`scale(${scale})`;
    $('stage').style.left=`${(innerWidth-1920*scale)/2}px`;
    $('stage').style.top=`${(innerHeight-1080*scale)/2}px`;
  }
  addEventListener('resize',resize);resize();
  await document.fonts.ready;
  const rio=new RioOrb($('rio-orb'),{pixels:1600});
  const speechData=await(await fetch('voice-envelope.json')).json();
  function speechEnvelope(t){
    const x=(t-6.05)*speechData.rate;
    if(x<0||x>=speechData.values.length-1)return 0;
    const i=Math.floor(x),p=x-i;
    return mix(speechData.values[i],speechData.values[i+1],p);
  }
  const copy=['Good','ideas',"shouldn’t",'have','to','wait','for','a','keyboard.'];
  $('typed-words').innerHTML=copy.map((w,i)=>`<span data-i="${i}">${w}${i<copy.length-1?' ':''}</span>`).join('');
  const typed=[...$('typed-words').children];
  const wordTimes=speechData.words.map(word=>6.05+word.at+.13);
  const meter=[...$('meter').children];
  const orbPath=[
    [0,1450,548,.05],[1.2,1450,548,3.75],[4.75,1450,548,3.75],
    [5.9,420,729,1.75],[9.95,420,729,1.75],
    [11.1,455,540,3.95],[14.9,455,540,3.95],
    [16.05,1467,545,3.35],[19.65,1467,545,3.35],
    [20.85,960,347,2.05],[24,960,347,2.05]
  ];
  window.renderFrame=function(t){
    t=Math.max(0,Math.min(24,t));
    scene('intro',t,.12,4.8);scene('demo',t,5.05,10.05);
    scene('model',t,10.35,15.05);scene('local',t,15.35,19.7);scene('close',t,20.03,25);
    const headerAlpha=fade(t,.2,.9)*(1-fade(t,19.7,.6));
    $('brand').style.opacity=headerAlpha;$('edition').style.opacity=headerAlpha*.7;
    reveal('intro-kicker',t,.4,{y:15,blur:0});
    reveal('intro-one',t,.65,{y:150,duration:.85,blur:2});
    reveal('intro-two',t,1.12,{y:150,duration:.9,blur:2});
    reveal('intro-note',t,1.9,{y:20,duration:.7,blur:0});
    reveal('demo-kicker',t,5.3,{y:20,blur:0});
    reveal('demo-title',t,5.48,{y:55,duration:.8,blur:3});
    reveal('dictation-card',t,5.5,{y:65,duration:.85,blur:4,scale:.95,bounce:.1});
    reveal('demo-caption',t,6.1,{y:12,duration:.6,blur:0});
    typed.forEach((element,i)=>{
      const age=t-wordTimes[i], p=spring(age,.22,.02);
      element.style.opacity=fade(t,wordTimes[i],.13);
      element.style.display=age<0?'none':'inline-block';
      element.style.transform=`translateY(${(1-p)*10}px)`;
      element.style.filter=`blur(${(1-ease(age/.25))*2}px)`;
    });
    $('caret').style.opacity=t<8.85?(Math.sin(t*6)>-.35?1:.1):.7;
    meter.forEach((bar,i)=>bar.style.transform=`scaleY(${.3+.8*Math.abs(Math.sin(t*8+i*1.3))*fade(t,6.1,.4)*(1-fade(t,8.8,.4))})`);
    reveal('model-kicker',t,10.6,{y:20,blur:0});
    reveal('model-name',t,10.8,{y:150,duration:.8,blur:2});
    reveal('model-two',t,11.1,{y:115,duration:.85,blur:3,scale:.85,bounce:.1});
    reveal('model-dot',t,11.3,{y:75,duration:.75,blur:2});
    reveal('model-five',t,11.47,{y:115,duration:.85,blur:3,scale:.85,bounce:.1});
    reveal('model-note',t,12.25,{y:20,duration:.7,blur:0});
    reveal('local-kicker',t,15.6,{y:20,blur:0});
    reveal('local-one',t,15.8,{y:145,duration:.8,blur:2});
    reveal('local-two',t,16.17,{y:145,duration:.8,blur:2});
    reveal('local-notes',t,16.9,{y:25,duration:.65,blur:0});
    reveal('local-foot',t,17.55,{y:15,duration:.65,blur:0});
    reveal('close-brand',t,20.4,{y:70,duration:.85,blur:5,scale:.97,bounce:.05});
    reveal('close-line',t,20.9,{y:25,duration:.75,blur:2});
    reveal('close-model',t,21.5,{y:18,duration:.7,blur:0});
    reveal('close-platform',t,22.15,{y:12,duration:.65,blur:0});
    const [x,y,size]=path(t,orbPath);
    const speech=speechEnvelope(t);
    const active=t>6.05&&t<10.2;
    const pulse=1+.008*Math.sin(t*1.7)+speech*.046;
    $('orb-stage').style.opacity=fade(t,.3,.75);
    // Shader sphere fills 60.8% of the 700px canvas. Size is specified in
    // scene units, so every hero still has about the original planned diameter.
    const scale=size*.235*pulse;
    $('orb-stage').style.transform=`translate3d(${x-350}px,${y-350}px,0) scale(${scale})`;
    const speakingHue=.51+.04*Math.sin(t*.65)+speech*.055;
    rio.draw({time:t,volume:speech,hue:active?speakingHue:.515,variant:0,listening:active});
    $('timeline').firstElementChild.style.transform=`scaleX(${t/24})`;
    document.documentElement.dataset.time=t.toFixed(4);
    return {time:t,proceduralOrb:true};
  };
  window.renderOrb=function(t=2.5,variant=0){
    [...document.querySelectorAll('.scene'),$('brand'),$('edition'),$('timeline')].forEach(el=>el.style.display='none');
    $('grain').style.display='none';$('lightField').style.display='none';
    $('orb-stage').style.opacity=1;
    $('orb-stage').style.transform='translate(610px,190px) scale(1.65)';
    rio.draw({time:t,volume:.38,hue:.515,variant});
  };
  window.FILM_READY=true;
  const exporting=new URLSearchParams(location.search).has('export');
  if(exporting)document.body.classList.add('export');
  else {
    let start=performance.now();
    function play(now){window.renderFrame(((now-start)/1000)%27>24?24:((now-start)/1000)%27);requestAnimationFrame(play)}
    $('play').onclick=()=>start=performance.now();requestAnimationFrame(play);
  }
  renderFrame(0);
})();
