/* Rio orb: a full-resolution procedural material. No image or screenshot is
   drawn into the canvas. Every pixel is shaded at the requested output size. */
(function(global){
const vertex=`attribute vec2 position;void main(){gl_Position=vec4(position,0.0,1.0);}`;
const fragment=`precision highp float;
uniform vec2 resolution;uniform float time;uniform float volume;uniform float hue;
uniform float variant;uniform float paused;uniform float listening;
const float PI=3.14159265359;
vec3 hsv(vec3 c){vec3 p=abs(fract(c.xxx+vec3(0.,2./3.,1./3.))*6.-3.);return c.z*mix(vec3(1.),clamp(p-1.,0.,1.),c.y);}
float smin(float a,float b,float k){float h=clamp(.5+.5*(b-a)/k,0.,1.);return mix(b,a,h)-k*h*(1.-h);}
float liquid(vec2 p,float t){
  float v=volume;
  float angle=.10*sin(t*.23)+.025*v*sin(t*1.8);
  p=mat2(cos(angle),-sin(angle),sin(angle),cos(angle))*p;
  p.y*=.96; p.x*=1.01;
  float a=length(p-vec2(.035*sin(t*.51),-.025+.018*cos(t*.43)))-(.335+.03*v);
  float b=length(p-vec2(-.15+.038*sin(t*.65),.18+.031*sin(t*.37)))-(.27+.026*v);
  float c=length(p-vec2(.17+.036*cos(t*.57),.16+.036*cos(t*.49)))-(.255+.032*v);
  float d=length(p-vec2(-.015+.03*sin(t*.39),-.16+.026*cos(t*.41)))-(.31+.016*v);
  float shape=smin(smin(a,b,.13),smin(c,d,.16),.14);
  shape+=.008*sin(p.y*12.+t*.7)*sin(p.x*10.-t*.55)*(1.+v);
  return shape;
}
float liquid3(vec3 p,float t){
  float a=.14*sin(t*.31);
  p.xz=mat2(cos(a),-sin(a),sin(a),cos(a))*p.xz;
  float v=volume;
  float aa=length(p-vec3(-.18+.045*sin(t*.7),.15+.043*cos(t*.53),.025*sin(t*.39)))-(.29+.025*v);
  float bb=length(p-vec3(.18+.045*cos(t*.61),.14+.043*sin(t*.49),.025*cos(t*.37)))-(.29+.03*v);
  float cc=length(p-vec3(.025*sin(t*.43),-.13+.025*cos(t*.59),.015))-(.35+.018*v);
  float dd=length(p-vec3(.025,-.015,.045))-(.32+.015*v);
  float shape=smin(smin(aa,bb,.10),smin(cc,dd,.15),.14);
  return shape+.015*sin(p.x*8.+t*1.1)*cos(p.y*10.-t*.8)*sin(p.z*7.+t*.4)*(1.+v);
}
void main(){
  vec2 p=(gl_FragCoord.xy/resolution-.5)*2.6;
  float r=length(p);float R=.79;
  float pixel=2.6/resolution.x;
  float outside=max(0.,r-R);
  float bloom=exp(-outside*outside*55.)*.085*step(R,r);
  float hueShift=hue+.005*sin(time*.16);
  vec3 cyan=hsv(vec3(hueShift,.82,1.));
  vec3 blue=hsv(vec3(hueShift+.065,.82,.84));
  vec3 teal=hsv(vec3(hueShift-.035,.94,.58));
  vec3 lilac=hsv(vec3(hueShift+.20,.42,1.));
  vec3 ink=vec3(.009,.027,.037);
  vec3 glow=mix(cyan,teal,smoothstep(.5,-.5,p.y));
  vec3 color=glow*(.55+volume*.3);
  float alpha=bloom*1.4;
  if(r<R+pixel*2.){
    float z=sqrt(max(0.,1.-r*r/(R*R)));
    vec3 n=vec3(p/R,z);
    float fresnel=pow(1.-z,3.2);
    float top=smoothstep(-.7,.65,p.y);
    vec3 shell=mix(ink,blue,.05+.24*top);
    shell+=cyan*fresnel*(.045+.18*top);
    shell+=teal*.018*smoothstep(.4,-.7,p.y);
    // Analytic softbox reflection and edge caustic; no blurred bitmap.
    float reflection=pow(max(0.,dot(n,normalize(vec3(-.47,.64,.62)))),42.);
    float reflection2=pow(max(0.,dot(n,normalize(vec3(.62,.03,.77)))),100.);
    shell+=vec3(.70,.91,1.)*reflection*.29;
    shell+=cyan*reflection2*.05;
    float rim=exp(-pow((r-R+.003)/(pixel+.0045),2.));
    shell+=mix(teal,vec3(.62,.96,1.),top)*rim*(.75+.32*top);
    float innerRim=exp(-pow((r-R+.017)/.007,2.));
    shell+=cyan*innerRim*.07;
    float t=time;
    vec3 hit=vec3(p*(1.+fresnel*.025),1.05);
    float edge=0.;
    for(int i=0;i<40;i++){
      float distance=liquid3(hit,t);
      if(distance<.0012){edge=1.;break;}
      hit.z-=max(distance,.0012);
      if(hit.z<-.8)break;
    }
    float eps=.002;
    vec3 bn=normalize(vec3(
      liquid3(hit+vec3(eps,0.,0.),t)-liquid3(hit-vec3(eps,0.,0.),t),
      liquid3(hit+vec3(0.,eps,0.),t)-liquid3(hit-vec3(0.,eps,0.),t),
      liquid3(hit+vec3(0.,0.,eps),t)-liquid3(hit-vec3(0.,0.,eps),t)));
    vec3 keyLight=normalize(vec3(-.6,.85,1.25));
    float lambert=max(0.,dot(bn,keyLight));
    float coreTop=smoothstep(-.46,.40,hit.y);
    vec3 core=mix(teal*.23,cyan*.85,coreTop);
    core*=.24+.76*lambert;
    core+=cyan*(.05+.13*coreTop);
    core+=cyan*.07*pow(1.-max(0.,bn.z),2.);
    float pearl=step(.5,variant)*(1.-step(1.5,variant));
    float aurora=step(1.5,variant);
    vec3 pearlCore=mix(blue*.35,mix(cyan,vec3(.82,.96,1.),.70),coreTop);
    pearlCore=mix(pearlCore,lilac,.18*smoothstep(-.1,.4,p.x)*coreTop);
    core=mix(core,pearlCore,pearl);
    vec3 auroraCore=mix(blue,mix(cyan,lilac,.45+.35*sin(t*.3+p.x*4.)),coreTop);
    core=mix(core,auroraCore,aurora);
    float spec=pow(max(0.,dot(reflect(-keyLight,bn),vec3(0.,0.,1.))),75.);
    float softSpec=pow(max(0.,dot(bn,normalize(vec3(-.3,.5,1.)))),16.);
    core+=mix(cyan,vec3(.9,1.,1.),.7)*spec*(.25+.17*pearl+.10*aurora);
    core+=mix(cyan,vec3(.8,1.,1.),.4)*softSpec*.16;
    core*=mix(.91,1.08,volume);
    shell=mix(shell,core,edge*.98);
    // Very faint reflected color under the floating liquid.
    shell+=teal*.025*exp(-length(p-vec2(0.,-.58))*10.);
    float cover=1.-smoothstep(R-pixel,R+pixel,r);
    color=mix(color,shell,cover);alpha=max(alpha,cover);
  }
  float gray=paused*.45;
  color=mix(color,vec3(dot(color,vec3(.2126,.7152,.0722))),gray);
  color=max(color,vec3(0.));
  gl_FragColor=vec4(color,alpha);
}`;
class RioOrb{
 constructor(canvas,{pixels=1400}={}){
  this.canvas=canvas;
  const gl=canvas.getContext('webgl',{alpha:true,premultipliedAlpha:false,antialias:true,preserveDrawingBuffer:true,powerPreference:'high-performance'});
  if(!gl)throw new Error('WebGL is required for the Rio procedural material.');
  this.gl=gl;canvas.width=canvas.height=pixels;
  const compile=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(s));return s;};
  const program=gl.createProgram();gl.attachShader(program,compile(gl.VERTEX_SHADER,vertex));gl.attachShader(program,compile(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(program);
  if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(program));
  this.program=program;gl.useProgram(program);
  const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
  const loc=gl.getAttribLocation(program,'position');gl.enableVertexAttribArray(loc);gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0);
  this.uniforms={};for(const key of ['resolution','time','volume','hue','variant','paused','listening'])this.uniforms[key]=gl.getUniformLocation(program,key);
  gl.viewport(0,0,pixels,pixels);
 }
 draw({time=0,volume=0,hue=.51,variant=0,paused=false,listening=false}={}){
  const gl=this.gl;gl.useProgram(this.program);
  gl.uniform2f(this.uniforms.resolution,this.canvas.width,this.canvas.height);
  for(const [key,value] of Object.entries({time,volume:Math.max(0,Math.min(1,volume)),hue,variant,paused:paused?1:0,listening:listening?1:0}))gl.uniform1f(this.uniforms[key],value);
  gl.drawArrays(gl.TRIANGLES,0,6);
 }
}
global.RioOrb=RioOrb;
})(window);
