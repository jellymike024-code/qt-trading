import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { MARKETS, CITY_ORDER, latLonVector } from './markets.js';

const RADIUS = 1.9;
const vector = (lat, lon, radius = RADIUS) => new THREE.Vector3(...latLonVector(lat, lon, radius));
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));

export class MarketGlobe {
  constructor({ canvas, container, labels, onSelect, onRotation, onReady, onError }) {
    this.canvas = canvas; this.container = container; this.labelsRoot = labels;
    this.onSelect = onSelect; this.onRotation = onRotation; this.onReady = onReady; this.onError = onError;
    this.selected = 'shanghai'; this.autoRotate = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.running = true; this.visible = true; this.angleTarget = null; this.pins = []; this.particles = [];
    try { this.init(); } catch (error) { this.fail(error); }
  }

  init() {
    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.75));
    this.renderer.setClearColor(0x030a15, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.3;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(41, 1, .1, 100);
    this.camera.position.set(0, 1.5, 6.75);
    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = true; this.controls.dampingFactor = .06; this.controls.enablePan = false;
    this.controls.minDistance = 5; this.controls.maxDistance = 10;
    this.controls.minPolarAngle = .25; this.controls.maxPolarAngle = Math.PI - .25;
    this.controls.rotateSpeed = .55; this.controls.zoomSpeed = .7;
    this.controls.target.set(0, .12, 0);
    this.controls.addEventListener('start', () => { this.angleTarget = null; this.setRotation(false); });
    this.earth = new THREE.Group(); this.earth.rotation.y = -MARKETS.shanghai.lon * Math.PI / 180 + .15;
    this.scene.add(this.earth);
    this.addStars(); this.addBaseGlobe(); this.addGrid(); this.addAtmosphere(); this.addOrbits(); this.addCities(); this.addConnections();
    this.raycaster = new THREE.Raycaster(); this.pointer = new THREE.Vector2();
    this.canvas.addEventListener('pointerdown', event => { this.down = [event.clientX, event.clientY]; });
    this.canvas.addEventListener('pointerup', event => this.pick(event));
    this.canvas.addEventListener('pointermove', event => this.hover(event));
    this.canvas.addEventListener('webglcontextlost', event => { event.preventDefault(); this.running = false; this.fail(new Error('WebGL上下文丢失，请刷新页面恢复渲染。')); });
    this.resizeObserver = new ResizeObserver(() => this.resize()); this.resizeObserver.observe(this.container);
    this.resize(); this.lastTime = performance.now();
    this.visibilityHandler = () => { this.visible = !document.hidden; this.lastTime = performance.now(); };
    document.addEventListener('visibilitychange', this.visibilityHandler);
    this.animate = this.animate.bind(this); this.frame = requestAnimationFrame(this.animate);
    this.loadAssets();
    this.onRotation?.(this.autoRotate);
  }

  addBaseGlobe() {
    const vertexShader = `varying vec2 vUv; varying vec3 vNormalWorld; varying vec3 vPositionWorld;
      void main(){vUv=uv;vNormalWorld=normalize(mat3(modelMatrix)*normal);vec4 world=modelMatrix*vec4(position,1.0);vPositionWorld=world.xyz;gl_Position=projectionMatrix*viewMatrix*world;}`;
    const fragmentShader = `uniform sampler2D dayMap;uniform sampler2D nightMap;uniform float hasTextures;
      varying vec2 vUv;varying vec3 vNormalWorld;varying vec3 vPositionWorld;
      void main(){vec3 n=normalize(vNormalWorld);vec3 viewDir=normalize(cameraPosition-vPositionWorld);
        vec3 sun=normalize(vec3(-3.0,3.5,6.0));float light=max(dot(n,sun),0.0);
        vec3 day=texture2D(dayMap,vUv).rgb;vec3 night=texture2D(nightMap,vUv).rgb;
        vec3 base=mix(vec3(0.008,0.035,0.085),day*vec3(0.31,0.57,0.87),hasTextures);
        vec3 color=base*(0.38+light*0.88)+night*vec3(0.38,0.69,1.0)*hasTextures*0.6;
        float fresnel=pow(1.0-max(dot(n,viewDir),0.0),3.5);
        color+=vec3(0.015,0.22,0.46)*fresnel;
        gl_FragColor=vec4(color,1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`;
    this.surfaceMaterial = new THREE.ShaderMaterial({ vertexShader, fragmentShader,
      uniforms: { dayMap: { value: new THREE.Texture() }, nightMap: { value: new THREE.Texture() }, hasTextures: { value: 0 } } });
    this.surface = new THREE.Mesh(new THREE.SphereGeometry(RADIUS, 96, 64), this.surfaceMaterial);
    // Three's UV Greenwich faces +X; rotate the texture sphere to Greenwich +Z,
    // keeping the marker/country lat-lon convention geographically aligned.
    this.surface.rotation.y = -Math.PI / 2;
    this.earth.add(this.surface);
  }

  async loadAssets() {
    const textureLoader = new THREE.TextureLoader();
    let textured = false;
    try {
      const [day, night] = await Promise.all([
        textureLoader.loadAsync('./assets/earth_day.jpg'), textureLoader.loadAsync('./assets/earth_night.png'),
      ]);
      for (const texture of [day, night]) { texture.colorSpace = THREE.SRGBColorSpace; texture.anisotropy = Math.min(4, this.renderer.capabilities.getMaxAnisotropy()); }
      this.surfaceMaterial.uniforms.dayMap.value = day; this.surfaceMaterial.uniforms.nightMap.value = night;
      this.surfaceMaterial.uniforms.hasTextures.value = 1;
      this.addLandPoints(day.image); textured = true;
    } catch (error) { console.warn('Earth textures unavailable; country geometry remains interactive.', error); }
    try {
      const response = await fetch('./assets/countries.geojson');
      if (!response.ok) throw new Error('Country geometry unavailable');
      this.addCountries(await response.json());
    } catch (error) { console.warn('Country outlines unavailable.', error); }
    this.onReady?.({ textured, renderer: this.renderer.capabilities.isWebGL2 ? 'WebGL 2' : 'WebGL' });
  }

  addAtmosphere() {
    const material = new THREE.ShaderMaterial({
      vertexShader: `varying vec3 n;varying vec3 p;void main(){n=normalize(mat3(modelMatrix)*normal);vec4 w=modelMatrix*vec4(position,1.);p=w.xyz;gl_Position=projectionMatrix*viewMatrix*w;}`,
      fragmentShader: `varying vec3 n;varying vec3 p;void main(){vec3 v=normalize(cameraPosition-p);float rim=pow(1.-abs(dot(normalize(n),v)),3.2);gl_FragColor=vec4(vec3(.025,.36,.92)*rim,rim*.7);}`,
      side: THREE.BackSide, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    });
    this.earth.add(new THREE.Mesh(new THREE.SphereGeometry(RADIUS * 1.065, 64, 48), material));
    const outer = material.clone(); outer.uniforms = {}; outer.opacity = .35;
    this.earth.add(new THREE.Mesh(new THREE.SphereGeometry(RADIUS * 1.1, 64, 48), outer));
  }

  addGrid() {
    const positions = [];
    for (let latitude = -75; latitude <= 75; latitude += 15) {
      for (let lon = -180; lon < 180; lon += 3) {
        positions.push(...vector(latitude, lon, RADIUS * 1.002).toArray(), ...vector(latitude, lon + 3, RADIUS * 1.002).toArray());
      }
    }
    for (let longitude = -180; longitude < 180; longitude += 15) {
      for (let lat = -90; lat < 90; lat += 3) {
        positions.push(...vector(lat, longitude, RADIUS * 1.003).toArray(), ...vector(lat + 3, longitude, RADIUS * 1.003).toArray());
      }
    }
    const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    this.earth.add(new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: 0x2584bd, transparent: true, opacity: .13, depthWrite: false })));
  }

  addCountries(data) {
    const positions = [], highlighted = [];
    for (const feature of data.features) {
      const geom = feature.geometry;
      if (!geom || !['Polygon', 'MultiPolygon'].includes(geom.type)) continue;
      const polygons = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
      const names = `${feature.properties.ADMIN || ''} ${feature.properties.NAME || ''}`;
      const destination = /China|South Korea|United Kingdom|United States/.test(names) ? highlighted : positions;
      for (const polygon of polygons) for (const ring of polygon) {
        for (let i = 1; i < ring.length; i++) {
          const [a, b] = [ring[i-1], ring[i]];
          // Densify long borders so line segments follow the globe surface.
          const va = vector(a[1], a[0], RADIUS * 1.006), vb = vector(b[1], b[0], RADIUS * 1.006);
          const steps = Math.max(1, Math.ceil(va.angleTo(vb) / .025));
          for (let j = 0; j < steps; j++) {
            const p = va.clone().lerp(vb, j / steps).normalize().multiplyScalar(RADIUS * 1.006);
            const q = va.clone().lerp(vb, (j+1) / steps).normalize().multiplyScalar(RADIUS * 1.006);
            destination.push(...p.toArray(), ...q.toArray());
          }
        }
      }
    }
    for (const [points, color, opacity] of [[positions, 0x2e93d4, .42], [highlighted, 0x8edbff, .8]]) {
      const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
      this.earth.add(new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false })));
    }
  }

  addLandPoints(image) {
    try {
      const canvas = document.createElement('canvas'); canvas.width = 512; canvas.height = 256;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      context.drawImage(image, 0, 0, 512, 256); const pixels = context.getImageData(0, 0, 512, 256).data;
      const points = [];
      for (let i = 0; i < 10500; i++) {
        const y = 1 - (i / 10499) * 2, lon = ((i * 137.507764) % 360) - 180;
        const lat = Math.asin(y) * 180 / Math.PI;
        const px = clamp(Math.floor((lon+180)/360*512), 0, 511), py = clamp(Math.floor((90-lat)/180*256), 0, 255);
        const index = (py*512+px)*4, [r,g,b] = [pixels[index], pixels[index+1], pixels[index+2]];
        if (b > r*1.18 && b > g*1.08 || lat < -65) continue;
        points.push(...vector(lat, lon, RADIUS * 1.009).toArray());
      }
      const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
      this.earth.add(new THREE.Points(geometry, new THREE.PointsMaterial({ color: 0x5cb9f0, size: .012, transparent: true, opacity: .44, depthWrite: false, sizeAttenuation: true })));
    } catch (error) { console.warn('Optional digital land points unavailable.', error); }
  }

  addStars() {
    let seed = 37; const random = () => { seed = (Math.imul(seed,1664525)+1013904223)>>>0; return seed/4294967296; };
    const points = [], colors = [];
    for (let i=0; i<1350; i++) {
      const latitude = Math.asin(random()*2-1), longitude = random()*Math.PI*2, radius = 14+random()*15;
      points.push(radius*Math.cos(latitude)*Math.sin(longitude), radius*Math.sin(latitude), radius*Math.cos(latitude)*Math.cos(longitude));
      const strength = .2+random()*.6; colors.push(strength*.3, strength*.67, strength);
    }
    const geometry = new THREE.BufferGeometry(); geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3)); geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors,3));
    this.stars = new THREE.Points(geometry, new THREE.PointsMaterial({ size: .035, transparent: true, opacity: .8, vertexColors: true, depthWrite: false }));
    this.scene.add(this.stars);
  }

  addOrbits() {
    for (let i=0; i<3; i++) {
      const radius = RADIUS*(1.18+i*.07), points = [];
      for (let j=0; j<=256; j++) { const a=j/256*Math.PI*2; points.push(new THREE.Vector3(Math.cos(a)*radius, 0, Math.sin(a)*radius)); }
      const geometry = new THREE.BufferGeometry().setFromPoints(points);
      const material = new THREE.LineDashedMaterial({ color: i===1?0x2774b0:0x3ea5df, transparent: true, opacity: i===1?.2:.36, dashSize: .05, gapSize: .045, depthWrite: false });
      const line = new THREE.Line(geometry, material); line.rotation.z = .2+i*.21; line.rotation.x = .12-i*.14; line.computeLineDistances(); this.scene.add(line);
    }
  }

  glowTexture() {
    if (this.glowMap) return this.glowMap;
    const canvas=document.createElement('canvas'); canvas.width=canvas.height=64;
    const ctx=canvas.getContext('2d'), grad=ctx.createRadialGradient(32,32,0,32,32,32);
    grad.addColorStop(0,'rgba(255,255,255,1)'); grad.addColorStop(.15,'rgba(160,223,255,.95)'); grad.addColorStop(.4,'rgba(80,175,255,.25)'); grad.addColorStop(1,'rgba(20,90,255,0)');
    ctx.fillStyle=grad;ctx.fillRect(0,0,64,64);this.glowMap=new THREE.CanvasTexture(canvas);return this.glowMap;
  }

  addCities() {
    for (const id of CITY_ORDER) {
      const market = MARKETS[id], color = new THREE.Color(market.color), base = vector(market.lat,market.lon,RADIUS*1.008), tip=vector(market.lat,market.lon,RADIUS*1.075);
      const stem=new THREE.Line(new THREE.BufferGeometry().setFromPoints([base,tip]),new THREE.LineBasicMaterial({color,transparent:true,opacity:.8}));this.earth.add(stem);
      const dot=new THREE.Mesh(new THREE.SphereGeometry(.022,12,12),new THREE.MeshBasicMaterial({color:0xc8f3ff}));dot.position.copy(tip);this.earth.add(dot);
      const glow=new THREE.Sprite(new THREE.SpriteMaterial({map:this.glowTexture(),color,transparent:true,blending:THREE.AdditiveBlending,depthWrite:false}));glow.position.copy(tip);glow.scale.setScalar(.19);this.earth.add(glow);
      const pulse=new THREE.Mesh(new THREE.RingGeometry(.045,.052,40),new THREE.MeshBasicMaterial({color,transparent:true,opacity:.65,side:THREE.DoubleSide,depthWrite:false}));
      pulse.position.copy(base);pulse.quaternion.setFromUnitVectors(new THREE.Vector3(0,0,1),base.clone().normalize());this.earth.add(pulse);
      const hit=new THREE.Mesh(new THREE.SphereGeometry(.115,12,12),new THREE.MeshBasicMaterial({transparent:true,opacity:0,depthWrite:false}));hit.position.copy(tip);hit.userData.cityId=id;this.earth.add(hit);
      const label=document.createElement('button');label.className=`city-label ${id===this.selected?'active':''}`;label.dataset.city=id;label.setAttribute('aria-label',`查看${market.city}股票市场与板块`);
      label.innerHTML=`<strong><i class="city-pin-dot"></i>${market.city}<span>${market.code}</span></strong><small>${market.indexEn}</small>`;
      label.addEventListener('pointerdown',e=>e.stopPropagation());label.addEventListener('click',()=>this.onSelect?.(id));this.labelsRoot.append(label);
      this.pins.push({id,base,tip,dot,glow,pulse,hit,label});
    }
  }

  addConnections() {
    const routes=[['shanghai','london'],['shanghai','newyork'],['shanghai','seoul'],['newyork','london'],['seoul','newyork']];
    routes.forEach(([from,to],index)=>{
      const a=vector(MARKETS[from].lat,MARKETS[from].lon,1),b=vector(MARKETS[to].lat,MARKETS[to].lon,1);
      const distance=a.angleTo(b),height=.12+distance*.25,points=[];
      for(let i=0;i<=80;i++){const t=i/80;const p=a.clone().lerp(b,t).normalize().multiplyScalar(RADIUS*1.022+Math.sin(Math.PI*t)*height);points.push(p);}
      const curve=new THREE.CatmullRomCurve3(points),color=index===1||index===3?0xfa647c:0x2eaeff;
      for(const [width,opacity] of [[.012,.09],[.0032,.65]]){
        this.earth.add(new THREE.Mesh(new THREE.TubeGeometry(curve,100,width,5,false),new THREE.MeshBasicMaterial({color,transparent:true,opacity,blending:THREE.AdditiveBlending,depthWrite:false})));
      }
      for(let j=0;j<2;j++){
        const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:this.glowTexture(),color,transparent:true,blending:THREE.AdditiveBlending,depthWrite:false}));sprite.scale.setScalar(.105);this.earth.add(sprite);
        this.particles.push({sprite,curve,phase:(index*.17+j*.47)%1,speed:.065+index*.01});
      }
    });
  }

  setRotation(value) { this.autoRotate=Boolean(value);this.onRotation?.(this.autoRotate); }
  focus(id, { initial = false } = {}) {
    if (!MARKETS[id] || !this.camera) return;
    this.selected=id;
    if (!initial) this.setRotation(false);
    let target=-MARKETS[id].lon*Math.PI/180;
    while(target-this.earth.rotation.y>Math.PI)target-=Math.PI*2;
    while(target-this.earth.rotation.y<-Math.PI)target+=Math.PI*2;
    this.angleTarget=target;
    this.cameraGoal=new THREE.Vector3(0,1.5,6.75);
    for(const pin of this.pins)pin.label.classList.toggle('active',pin.id===id);
  }
  reset() { if(!this.controls)return;this.focus('shanghai');this.controls.target.set(0,.12,0);this.setRotation(true); }
  zoom(direction) { if(!this.camera)return;const offset=this.camera.position.clone().sub(this.controls.target);const distance=clamp(offset.length()*(direction>0?.87:1.15),this.controls.minDistance,this.controls.maxDistance);this.camera.position.copy(this.controls.target).add(offset.normalize().multiplyScalar(distance));this.cameraGoal=null; }

  intersection(event) {
    if(!this.raycaster)return null;
    const rect=this.canvas.getBoundingClientRect();this.pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);
    this.raycaster.setFromCamera(this.pointer,this.camera);
    const hits=this.raycaster.intersectObjects(this.pins.map(p=>p.hit));
    for(const hit of hits){const pin=this.pins.find(p=>p.id===hit.object.userData.cityId),world=this.earth.localToWorld(pin.tip.clone());if(world.clone().normalize().dot(this.camera.position.clone().sub(world).normalize())>.08)return pin.id;}
    return null;
  }
  pick(event) { if(!this.down||Math.hypot(event.clientX-this.down[0],event.clientY-this.down[1])>6)return;const id=this.intersection(event);if(id)this.onSelect?.(id); }
  hover(event) { if(this.down&&event.buttons)return;this.canvas.style.cursor=this.intersection(event)?'pointer':'grab'; }
  resize() { const {width,height}=this.container.getBoundingClientRect();if(!width||!height)return;this.width=width;this.height=height;this.camera.aspect=width/height;this.camera.updateProjectionMatrix();this.renderer.setSize(width,height,false); }

  animate(now) {
    if(!this.running)return;
    this.frame=requestAnimationFrame(this.animate);if(!this.visible)return;
    const delta=Math.min((now-this.lastTime)/1000,.05);this.lastTime=now;
    if(this.angleTarget!==null){const difference=this.angleTarget-this.earth.rotation.y;this.earth.rotation.y+=difference*(1-Math.exp(-delta*4.7));if(Math.abs(difference)<.001)this.angleTarget=null;}
    else if(this.autoRotate)this.earth.rotation.y+=delta*.037;
    if(this.cameraGoal){this.camera.position.lerp(this.cameraGoal,1-Math.exp(-delta*5));if(this.camera.position.distanceTo(this.cameraGoal)<.005)this.cameraGoal=null;}
    const seconds=now/1000;
    for(const pin of this.pins){const pulse=(seconds*.5+CITY_ORDER.indexOf(pin.id)*.21)%1;pin.pulse.scale.setScalar(1+pulse*1.8);pin.pulse.material.opacity=(1-pulse)*.55;pin.glow.scale.setScalar((pin.id === this.selected ? .21 : .16)*(1+Math.sin(seconds*2)*.08));}
    if(!matchMedia('(prefers-reduced-motion: reduce)').matches)for(const particle of this.particles)particle.sprite.position.copy(particle.curve.getPoint((seconds*particle.speed+particle.phase)%1));
    this.controls.update();this.earth.updateMatrixWorld(true);
    for(const pin of this.pins){
      const world=this.earth.localToWorld(pin.tip.clone()),normal=world.clone().normalize(),toCamera=this.camera.position.clone().sub(world).normalize(),projected=world.clone().project(this.camera);
      const front=normal.dot(toCamera)>.10&&projected.z<1&&Math.abs(projected.x)<1.12&&Math.abs(projected.y)<.9;
      pin.label.hidden=!front;
      if(front){const offset=pin.id==='seoul'?[9,-17]:pin.id==='shanghai'?[21,34]:[0,-13];pin.label.style.left=`${clamp((projected.x*.5+.5)*this.width+offset[0],62,this.width-62)}px`;pin.label.style.top=`${(1-(projected.y*.5+.5))*this.height+offset[1]}px`;}
    }
    this.renderer.render(this.scene,this.camera);
  }

  fail(error) { console.error('WebGL initialization failed',error);this.onError?.(error); }
  dispose() {this.running=false;cancelAnimationFrame(this.frame);this.resizeObserver?.disconnect();this.controls?.dispose();this.renderer?.dispose();document.removeEventListener('visibilitychange',this.visibilityHandler);}
}
