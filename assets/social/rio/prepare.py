"""Initialize the revised Rio film from the editable timeline, replacing its
low-resolution orb with a new procedural WebGL material and voice envelope."""
from pathlib import Path

here=Path(__file__).parent
old=here.parent/'motion'
html=(old/'index.html').read_text(encoding='utf-8').replace('Syllink','Rio').replace('SYLLINK','RIO')
html=html.replace('<link rel="stylesheet" href="orb.css">','<script src="rio-orb.js"></script>')
html=html.replace('<title>','<link rel="icon" href="data:,"><title>')
html=html.replace('<div id="orb-anchor"></div>','<canvas id="rio-orb" aria-label="Live procedural Rio orb"></canvas>')
html=html.replace('LESS BETWEEN YOU AND YOUR WORDS.','MEET RIO. A LITTLE MORE FLOW.')
html=html.replace('A NEW VOICE ENGINE, INSIDE RIO.','NOW SPEAKING PHONON 2.5.')
(here/'index.html').write_text(html,encoding='utf-8')
css=(old/'film.css').read_text(encoding='utf-8')
css+='''
/* Native shader canvas: the tiny app bitmap/mask is never scaled here. */
#orb-stage{width:700px;height:700px;transform-origin:50% 50%;}
#rio-orb{display:block;width:700px;height:700px;}
#close-brand{font-size:204px;letter-spacing:-12px;top:485px;}
header{letter-spacing:4.8px;font-size:28px;}
#grain{opacity:.06;}#orb-halo{display:none;}
'''
(here/'film.css').write_text(css,encoding='utf-8')
js=(old/'film.js').read_text(encoding='utf-8')
js=js.replace("const wordTimes=[6.25,6.55,6.98,7.27,7.45,7.78,8.11,8.26,8.62];", "const wordTimes=speechData.words.map(word=>6.05+word.at+.13);")
js=js.replace('orbAnimations:orbAnimations.length','proceduralOrb:true')
start=js.index("  $('orb-anchor').innerHTML=")
end=js.index('  const copy=',start)
js=js[:start]+'''  await document.fonts.ready;
  const rio=new RioOrb($('rio-orb'),{pixels:1600});
  const speechData=await(await fetch('voice-envelope.json')).json();
  function speechEnvelope(t){
    const x=(t-6.05)*speechData.rate;
    if(x<0||x>=speechData.values.length-1)return 0;
    const i=Math.floor(x),p=x-i;
    return mix(speechData.values[i],speechData.values[i+1],p);
  }
'''+js[end:]
start=js.index('    const speech=fade(t,6.0')
end=js.index("    $('timeline')",start)
js=js[:start]+'''    const speech=speechEnvelope(t);
    const active=t>6.05&&t<10.2;
    const pulse=1+.008*Math.sin(t*1.7)+speech*.046;
    $('orb-stage').style.opacity=fade(t,.3,.75);
    // Shader sphere fills 60.8% of the 700px canvas. Size is specified in
    // scene units, so every hero still has about the original planned diameter.
    const scale=size*.235*pulse;
    $('orb-stage').style.transform=`translate3d(${x-350}px,${y-350}px,0) scale(${scale})`;
    const speakingHue=.51+.04*Math.sin(t*.65)+speech*.055;
    rio.draw({time:t,volume:speech,hue:active?speakingHue:.515,variant:0,listening:active});
'''+js[end:]
start=js.index('  window.renderOrb=')
end=js.index('  window.FILM_READY',start)
js=js[:start]+'''  window.renderOrb=function(t=2.5,variant=0){
    [...document.querySelectorAll('.scene'),$('brand'),$('edition'),$('timeline')].forEach(el=>el.style.display='none');
    $('grain').style.display='none';$('lightField').style.display='none';
    $('orb-stage').style.opacity=1;
    $('orb-stage').style.transform='translate(610px,190px) scale(1.65)';
    rio.draw({time:t,volume:.38,hue:.515,variant});
  };
'''+js[end:]
(here/'film.js').write_text(js,encoding='utf-8')
(here/'serve.cjs').write_text((old/'serve.cjs').read_text(encoding='utf-8').replace('Syllink','Rio').replace('4175','4176'),encoding='utf-8')
print('Prepared Rio film. Shader replaces the old app mask completely.')
