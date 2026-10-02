"use client";
import { useEffect, useRef } from "react";
import * as THREE from "three";

export type OrbState = "idle" | "listening" | "thinking" | "speaking";

const vert = /* glsl */ `
uniform float uTime; uniform float uLevel; uniform float uMode; uniform vec2 uMouse; uniform float uPx;
attribute float aSeed; varying float vN; varying float vSeed;
vec3 mod289(vec3 x){return x-floor(x*(1./289.))*289.;} vec4 mod289(vec4 x){return x-floor(x*(1./289.))*289.;}
vec4 perm(vec4 x){return mod289(((x*34.)+1.)*x);} vec4 tis(vec4 r){return 1.79284291400159-.85373472095314*r;}
float snoise(vec3 v){const vec2 C=vec2(1./6.,1./3.);const vec4 D=vec4(0.,.5,1.,2.);
 vec3 i=floor(v+dot(v,C.yyy));vec3 x0=v-i+dot(i,C.xxx);vec3 g=step(x0.yzx,x0.xyz);vec3 l=1.-g;vec3 i1=min(g.xyz,l.zxy);vec3 i2=max(g.xyz,l.zxy);
 vec3 x1=x0-i1+C.xxx;vec3 x2=x0-i2+C.yyy;vec3 x3=x0-D.yyy;i=mod289(i);
 vec4 p=perm(perm(perm(i.z+vec4(0.,i1.z,i2.z,1.))+i.y+vec4(0.,i1.y,i2.y,1.))+i.x+vec4(0.,i1.x,i2.x,1.));
 float n_=.142857142857;vec3 ns=n_*D.wyz-D.xzx;vec4 j=p-49.*floor(p*ns.z*ns.z);vec4 x_=floor(j*ns.z);vec4 y_=floor(j-7.*x_);
 vec4 x=x_*ns.x+ns.yyyy;vec4 y=y_*ns.x+ns.yyyy;vec4 h=1.-abs(x)-abs(y);vec4 b0=vec4(x.xy,y.xy);vec4 b1=vec4(x.zw,y.zw);
 vec4 s0=floor(b0)*2.+1.;vec4 s1=floor(b1)*2.+1.;vec4 sh=-step(h,vec4(0.));vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
 vec3 p0=vec3(a0.xy,h.x);vec3 p1=vec3(a0.zw,h.y);vec3 p2=vec3(a1.xy,h.z);vec3 p3=vec3(a1.zw,h.w);
 vec4 nrm=tis(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));p0*=nrm.x;p1*=nrm.y;p2*=nrm.z;p3*=nrm.w;
 vec4 m=max(.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.);m=m*m;return 42.*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));}
void main(){
  vec3 p=position; float t=uTime;
  float speed = uMode==3. ? 1.4 : uMode==2. ? 2.2 : uMode==1. ? .7 : .35;
  float n = snoise(p*1.15 + vec3(0., t*speed, t*.2*speed));
  float amp = .10 + uLevel*.42 + (uMode==2. ? .12 : 0.);
  float swirl = uMode==2. ? t*1.2 : 0.;
  float c=cos(swirl*p.y*.35), s=sin(swirl*p.y*.35); p.xz = mat2(c,-s,s,c)*p.xz;
  p += normalize(position) * n * amp;
  p.xy += uMouse * .12 * (1.-aSeed*.4);
  vN=n; vSeed=aSeed;
  vec4 mv = modelViewMatrix*vec4(p,1.);
  gl_PointSize = uPx * (0.9 + aSeed*1.3 + uLevel*1.4) * (4.2/-mv.z);
  gl_Position = projectionMatrix*mv;
}`;
const frag = /* glsl */ `
uniform float uMode; uniform float uTime; varying float vN; varying float vSeed;
void main(){
  vec2 uv=gl_PointCoord-.5; float d=length(uv); if(d>.5) discard;
  float a=smoothstep(.5,.0,d);
  vec3 amber=vec3(1.,.62,.37), rose=vec3(1.,.30,.55), violet=vec3(.49,.36,1.), cyan=vec3(.36,.88,.9);
  float k=vN*.5+.5;
  vec3 col = mix(amber, rose, smoothstep(.2,.55,k)); col = mix(col, violet, smoothstep(.55,.95,k));
  if(uMode==1.) col = mix(col, cyan, .55);
  col += vSeed*.15;
  float depthFade = smoothstep(-1.2, 1.2, -gl_FragCoord.z*0.0 + vN*.0 + 1.0);
  gl_FragColor = vec4(col*0.85, a*(.30+vSeed*.35)*depthFade);
}`;

type Props = { state?: OrbState; level?: number; className?: string; autoCycle?: boolean; density?: number };

/** Particle orb. `state` selects the motion; `level` (0..1) is audio energy. When `autoCycle`, it demos
 * idle -> listening -> thinking -> speaking on its own with a synthetic speech envelope. */
export default function Orb({ state = "idle", level = 0, className = "", autoCycle = false, density = 1 }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const live = useRef({ state, level });
  live.current.state = state; live.current.level = level;

  useEffect(() => {
    const el = host.current; if (!el) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "high-performance" }); } catch { return; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    el.appendChild(renderer.domElement);
    renderer.domElement.style.cssText = "width:100%;height:100%;display:block";
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 50); camera.position.z = 4.2;

    const N = Math.floor((window.innerWidth < 700 ? 9000 : 18000) * density);
    const pos = new Float32Array(N * 3), seed = new Float32Array(N);
    const ga = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < N; i++) {
      const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = ga * i;
      pos.set([Math.cos(th) * r, y, Math.sin(th) * r], i * 3); seed[i] = Math.random();
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geo.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
    const mat = new THREE.ShaderMaterial({
      vertexShader: vert, fragmentShader: frag, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uTime: { value: 0 }, uLevel: { value: 0 }, uMode: { value: 0 }, uMouse: { value: new THREE.Vector2() }, uPx: { value: renderer.getPixelRatio() * 1.15 } },
    });
    const pts = new THREE.Points(geo, mat); scene.add(pts);

    const resize = () => { const w = el.clientWidth || 1, h = el.clientHeight || 1; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); };
    resize(); const ro = new ResizeObserver(resize); ro.observe(el);
    const mouse = new THREE.Vector2(), tgt = new THREE.Vector2();
    const onMove = (e: PointerEvent) => { const b = el.getBoundingClientRect(); tgt.set(((e.clientX - b.left) / b.width - .5) * 2, -((e.clientY - b.top) / b.height - .5) * 2); };
    window.addEventListener("pointermove", onMove);

    let visible = true; const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; }); io.observe(el);
    const modes: Record<OrbState, number> = { idle: 0, listening: 1, thinking: 2, speaking: 3 };
    const cycle: OrbState[] = ["idle", "listening", "thinking", "speaking"], dur = [2.2, 2.6, 1.8, 4.2];
    let raf = 0, t0 = performance.now(), lvl = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick); if (!visible) return;
      const t = (performance.now() - t0) / 1000;
      let st = live.current.state, target = live.current.level;
      if (autoCycle) {
        const total = dur.reduce((a, b) => a + b, 0); let m = t % total, i = 0; while (m > dur[i]) { m -= dur[i]; i++; }
        st = cycle[i];
        target = st === "speaking" ? .35 + .35 * Math.abs(Math.sin(t * 7.3) * Math.sin(t * 2.1 + 1)) : st === "listening" ? .12 + .1 * Math.abs(Math.sin(t * 4)) : 0;
      }
      lvl += (target - lvl) * .12; mouse.lerp(tgt, .05);
      mat.uniforms.uTime.value = reduce ? 0 : t; mat.uniforms.uLevel.value = lvl; mat.uniforms.uMode.value = modes[st]; mat.uniforms.uMouse.value.copy(mouse);
      pts.rotation.y = reduce ? 0 : t * .08 + mouse.x * .25; pts.rotation.x = mouse.y * -.15;
      renderer.render(scene, camera);
    };
    tick();
    return () => { cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); window.removeEventListener("pointermove", onMove); geo.dispose(); mat.dispose(); renderer.dispose(); renderer.domElement.remove(); };
  }, [autoCycle, density]);

  return <div ref={host} className={className} aria-hidden />;
}
