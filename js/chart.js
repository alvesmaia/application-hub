(function(){
'use strict';

const W=560,H=200,PAD={top:28,right:14,bottom:26,left:38};
const PLOT_W=W-PAD.left-PAD.right,PLOT_H=H-PAD.top-PAD.bottom;
const registry=new Map();
const reduceMotionQuery=window.matchMedia?window.matchMedia('(prefers-reduced-motion: reduce)'):null;
function prefersReducedMotion(){return Boolean(reduceMotionQuery&&reduceMotionQuery.matches)}

function niceNum(range,round){
  const exponent=Math.floor(Math.log10(range||1)),fraction=(range||1)/Math.pow(10,exponent);
  let niceFraction;
  if(round){niceFraction=fraction<1.5?1:fraction<3?2:fraction<7?5:10}
  else{niceFraction=fraction<=1?1:fraction<=2?2:fraction<=5?5:10}
  return niceFraction*Math.pow(10,exponent)
}
function niceScale(min,max,tickCount){
  if(min===max){min=min-1;max=max+1}
  const range=niceNum(max-min,false),step=niceNum(range/(tickCount-1),true)||1;
  const niceMin=Math.floor(min/step)*step,niceMax=Math.ceil(max/step)*step;
  const ticks=[];
  for(let v=niceMin;v<=niceMax+step*1e-6;v+=step)ticks.push(Math.round(v*1000)/1000);
  return{min:niceMin,max:niceMax,ticks}
}

// Fritsch-Carlson monotone cubic tangents: keeps the curve inside the local
// min/max of each segment so the spline never overshoots real data values.
function monotoneTangents(xs,ys){
  const n=xs.length,d=[],m=new Array(n);
  for(let i=0;i<n-1;i++)d[i]=(ys[i+1]-ys[i])/(xs[i+1]-xs[i]);
  m[0]=d[0];m[n-1]=d[n-2];
  for(let i=1;i<n-1;i++)m[i]=(d[i-1]<=0&&d[i]>=0)||(d[i-1]>=0&&d[i]<=0)?0:(d[i-1]+d[i])/2;
  for(let i=0;i<n-1;i++){
    if(d[i]===0){m[i]=0;m[i+1]=0;continue}
    const a=m[i]/d[i],b=m[i+1]/d[i],s=a*a+b*b;
    if(s>9){const t=3/Math.sqrt(s);m[i]=t*a*d[i];m[i+1]=t*b*d[i]}
  }
  return m
}
function splinePath(xs,ys){
  if(xs.length<2)return'';
  const m=monotoneTangents(xs,ys);
  let d=`M${xs[0]},${ys[0]}`;
  for(let i=0;i<xs.length-1;i++){
    const dx=(xs[i+1]-xs[i])/3;
    d+=` C${xs[i]+dx},${ys[i]+m[i]*dx} ${xs[i+1]-dx},${ys[i+1]-m[i+1]*dx} ${xs[i+1]},${ys[i+1]}`
  }
  return d
}
function xAt(i,n){return PAD.left+(n<=1?0:i/(n-1)*PLOT_W)}
function yAt(v,scale){return PAD.top+(1-(v-scale.min)/(scale.max-scale.min||1))*PLOT_H}

function seriesScale(series){
  const values=series.points;
  return niceScale(Math.min(0,...values),Math.max(...values),5)
}
function seriesGeometry(series){
  const scale=seriesScale(series),n=series.points.length;
  const xs=series.points.map((_,i)=>xAt(i,n)),ys=series.points.map(v=>yAt(v,scale));
  return{scale,xs,ys}
}

function fmtNumber(value,locale){return Math.round(value).toLocaleString(locale||'pt-BR')}
function fmtChange(value){return`${value>0?'+':value<0?'−':''}${Math.abs(value).toFixed(1).replace('.',',')}%`}
function escapeAttr(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

function gridAndAxisSVG(scale,periods){
  const grid=scale.ticks.map(tick=>{
    const y=yAt(tick,scale);
    return`<line class="tsc-grid-line" x1="${PAD.left}" y1="${y}" x2="${W-PAD.right}" y2="${y}"/><text class="tsc-y-label" x="${PAD.left-6}" y="${y}" text-anchor="end" dominant-baseline="middle">${fmtNumber(tick)}</text>`
  }).join('');
  const count=periods.length,xLabels=periods.map((label,i)=>{
    const x=PAD.left+(count<=1?0:i/(count-1)*PLOT_W);
    return`<text class="tsc-x-label" x="${x}" y="${H-6}" text-anchor="${i===0?'start':i===count-1?'end':'middle'}">${escapeAttr(label)}</text>`
  }).join('');
  return{grid,xLabels}
}

function pointsAndLabelsSVG(xs,ys,points,unit){
  const circles=xs.map((x,i)=>`<circle class="tsc-point" data-i="${i}" cx="${x}" cy="${ys[i]}" r="3"/>`).join('');
  const labels=xs.map((x,i)=>`<text class="tsc-value-label" data-i="${i}" x="${x}" y="${ys[i]-9}" text-anchor="middle">${fmtNumber(points[i])}</text>`).join('');
  return{circles,labels}
}

function renderCategoryRow(cat,mainTotal){
  const percent=mainTotal?Math.round(cat.total/mainTotal*100):0,changeClass=cat.change<0?'down':'up';
  return`<button type="button" class="tsc-cat-row" data-tsc-cat="${escapeAttr(cat.key)}" style="--tsc-cat-color:${escapeAttr(cat.color)}" aria-pressed="false">
    <span class="tsc-cat-top"><span class="tsc-cat-name"><i class="tsc-cat-swatch"></i>${escapeAttr(cat.name)}</span><span class="tsc-cat-change ${changeClass}">${fmtChange(cat.change)}</span></span>
    <span class="tsc-cat-mid"><strong class="tsc-cat-value" data-tsc-cat-value>${fmtNumber(cat.total)}</strong><span class="tsc-cat-percent" data-tsc-cat-percent>${percent}%</span></span>
    <span class="tsc-cat-bar"><span data-tsc-cat-bar style="width:${percent}%"></span></span>
  </button>`
}

function ariaLabel(config,series){
  return`Gráfico de ${config.title||'indicador'}, série ${series.name}, total de ${fmtNumber(series.total)} ${config.unit||''}, variação de ${fmtChange(series.change)} em relação ao período anterior.`
}

function render(config){
  registry.set(config.id,config);
  const{scale,xs,ys}=seriesGeometry(config.main);
  const{grid,xLabels}=gridAndAxisSVG(scale,config.periods);
  const{circles,labels}=pointsAndLabelsSVG(xs,ys,config.main.points,config.unit);
  const areaD=`${splinePath(xs,ys)} L${xs[xs.length-1]},${PAD.top+PLOT_H} L${xs[0]},${PAD.top+PLOT_H} Z`;
  const changeClass=config.main.change<0?'down':'up';
  const gradientId=`tsc-fill-${config.id}`;
  const categoriesHTML=config.categories.map(cat=>renderCategoryRow(cat,config.main.total)).join('');
  return`<div class="dashboard-grid">
  <section class="chart-panel tsc" data-tsc data-tsc-id="${config.id}" style="--tsc-series-color:${config.main.color}">
    <div class="chart-title">
      <h3>${escapeAttr(config.title||'')}</h3>
      <div class="asset-chart-summary">
        <span class="tsc-series-name" data-tsc-series-name>${escapeAttr(config.main.name)}</span>
        <span class="tsc-total"><span class="tsc-total-value" data-tsc-value>${fmtNumber(config.main.total)}</span> ${escapeAttr(config.unit||'')}</span>
        <span class="tsc-change ${changeClass}" data-tsc-change>${fmtChange(config.main.change)}</span>
      </div>
    </div>
    <div class="tsc-chart-wrap">
      <svg class="tsc-svg" data-tsc-svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${escapeAttr(ariaLabel(config,config.main))}">
        <defs><linearGradient id="${gradientId}" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="${config.main.color}" stop-opacity=".22"/>
          <stop offset="100%" stop-color="${config.main.color}" stop-opacity="0"/>
        </linearGradient></defs>
        <g class="tsc-grid" data-tsc-grid>${grid}</g>
        <path class="tsc-area" data-tsc-area d="${areaD}" fill="url(#${gradientId})"/>
        <path class="tsc-line" data-tsc-line d="${splinePath(xs,ys)}" stroke="${config.main.color}"/>
        <g class="tsc-points" data-tsc-points fill="${config.main.color}" stroke="${config.main.color}">${circles}</g>
        <g class="tsc-labels" data-tsc-value-labels>${labels}</g>
        <g class="tsc-xaxis" data-tsc-xaxis>${xLabels}</g>
        <rect class="tsc-hit" data-tsc-hit x="${PAD.left}" y="0" width="${PLOT_W}" height="${H}" fill="transparent"/>
        <g class="tsc-focus" data-tsc-focus hidden aria-hidden="true"><line data-tsc-focus-line/><circle data-tsc-focus-dot r="4"/></g>
      </svg>
      <div class="tsc-tooltip" data-tsc-tooltip hidden></div>
    </div>
  </section>
  <section class="chart-panel tsc-categories" data-tsc-categories data-tsc-id="${config.id}">
    <div class="chart-title"><h3>Por categoria</h3><span>Este mês</span></div>
    <div class="tsc-cat-list" data-tsc-cat-list role="group" aria-label="Categorias de ${escapeAttr(config.title||'')}">${categoriesHTML}</div>
  </section>
</div>`
}

function easeInOutCubic(t){return t<0.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2}

function tweenNumber(el,from,to,duration,locale,onFrame){
  const start=performance.now();
  function frame(now){
    const ratio=Math.min((now-start)/duration,1),eased=easeInOutCubic(ratio),value=from+(to-from)*eased;
    el.textContent=fmtNumber(value,locale);
    if(onFrame)onFrame(eased);
    if(ratio<1)requestAnimationFrame(frame)
  }
  requestAnimationFrame(frame)
}

function mount(container,config){
  if(container.dataset.tscBound==='1')return;
  container.dataset.tscBound='1';
  const catContainer=document.querySelector(`[data-tsc-categories][data-tsc-id="${config.id}"]`);
  const svg=container.querySelector('[data-tsc-svg]'),line=container.querySelector('[data-tsc-line]'),area=container.querySelector('[data-tsc-area]');
  const gradientStops=svg.querySelectorAll('linearGradient stop');
  const grid=container.querySelector('[data-tsc-grid]'),xaxis=container.querySelector('[data-tsc-xaxis]'),valueLabels=container.querySelector('[data-tsc-value-labels]');
  const points=container.querySelector('[data-tsc-points]'),hit=container.querySelector('[data-tsc-hit]');
  const focusGroup=container.querySelector('[data-tsc-focus]'),focusLine=focusGroup.querySelector('[data-tsc-focus-line]'),focusDot=focusGroup.querySelector('[data-tsc-focus-dot]');
  const tooltip=container.querySelector('[data-tsc-tooltip]'),totalEl=container.querySelector('[data-tsc-value]'),changeEl=container.querySelector('[data-tsc-change]'),seriesNameEl=container.querySelector('[data-tsc-series-name]');
  let current=config.main,animId=0;

  function setAria(series){svg.setAttribute('aria-label',ariaLabel(config,series))}

  function paintStatic(series,scale,xs,ys){
    const{grid:gridHTML,xLabels}=gridAndAxisSVG(scale,config.periods);
    grid.innerHTML=gridHTML;xaxis.innerHTML=xLabels;
    valueLabels.innerHTML=series.points.map((v,i)=>`<text class="tsc-value-label" data-i="${i}" x="${xs[i]}" y="${ys[i]-9}" text-anchor="middle">${fmtNumber(v)}</text>`).join('');
    points.innerHTML=xs.map((x,i)=>`<circle class="tsc-point" data-i="${i}" cx="${x}" cy="${ys[i]}" r="3"/>`).join('');
    points.setAttribute('fill',series.color);points.setAttribute('stroke',series.color);
    line.setAttribute('stroke',series.color);
    gradientStops.forEach(s=>s.setAttribute('stop-color',series.color));
    line.setAttribute('d',splinePath(xs,ys));
    area.setAttribute('d',`${splinePath(xs,ys)} L${xs[xs.length-1]},${PAD.top+PLOT_H} L${xs[0]},${PAD.top+PLOT_H} Z`);
    container.style.setProperty('--tsc-series-color',series.color)
  }

  function animateTo(series){
    const reduced=prefersReducedMotion();
    const fromGeo=seriesGeometry(current),toGeo=seriesGeometry(series);
    const duration=reduced?0:600,startTime=performance.now(),token=++animId;
    const fromTotal=current.total,fromChange=current.change;
    if(changeEl)changeEl.classList.toggle('down',series.change<0);
    if(changeEl)changeEl.classList.toggle('up',series.change>=0);
    if(seriesNameEl)seriesNameEl.textContent=series.name;
    setAria(series);
    if(reduced||duration===0){
      paintStatic(series,toGeo.scale,toGeo.xs,toGeo.ys);
      if(totalEl)totalEl.textContent=fmtNumber(series.total);
      if(changeEl)changeEl.textContent=fmtChange(series.change);
      current=series;
      return
    }
    function frame(now){
      if(token!==animId)return;
      const ratio=Math.min((now-startTime)/duration,1),eased=easeInOutCubic(ratio);
      const scale={min:fromGeo.scale.min+(toGeo.scale.min-fromGeo.scale.min)*eased,max:fromGeo.scale.max+(toGeo.scale.max-fromGeo.scale.max)*eased};
      const n=series.points.length,xs=fromGeo.xs.map((x,i)=>x+(toGeo.xs[i]-x)*eased);
      const interpValues=series.points.map((_,i)=>{
        const fromValue=current.points[i]!==undefined?current.points[i]:series.points[i];
        return fromValue+(series.points[i]-fromValue)*eased
      });
      const ys=interpValues.map(v=>PAD.top+(1-(v-scale.min)/((scale.max-scale.min)||1))*PLOT_H);
      const ticks=niceScale(scale.min,scale.max,5);
      const{grid:gridHTML,xLabels}=gridAndAxisSVG(ticks,config.periods);
      grid.innerHTML=gridHTML;xaxis.innerHTML=xLabels;
      valueLabels.innerHTML=interpValues.map((v,i)=>`<text class="tsc-value-label" data-i="${i}" x="${xs[i]}" y="${ys[i]-9}" text-anchor="middle">${fmtNumber(v)}</text>`).join('');
      points.innerHTML=xs.map((x,i)=>`<circle class="tsc-point" data-i="${i}" cx="${x}" cy="${ys[i]}" r="3"/>`).join('');
      const color=eased<1?blendColor(current.color,series.color,eased):series.color;
      points.setAttribute('fill',color);points.setAttribute('stroke',color);line.setAttribute('stroke',color);gradientStops.forEach(s=>s.setAttribute('stop-color',color));
      const d=splinePath(xs,ys);
      line.setAttribute('d',d);area.setAttribute('d',`${d} L${xs[n-1]},${PAD.top+PLOT_H} L${xs[0]},${PAD.top+PLOT_H} Z`);
      container.style.setProperty('--tsc-series-color',color);
      if(totalEl)totalEl.textContent=fmtNumber(fromTotal+(series.total-fromTotal)*eased);
      if(changeEl)changeEl.textContent=fmtChange(fromChange+(series.change-fromChange)*eased);
      if(ratio<1)requestAnimationFrame(frame);else current=series
    }
    requestAnimationFrame(frame)
  }

  function blendColor(a,b,t){
    const pa=hexToRgb(a),pb=hexToRgb(b);
    if(!pa||!pb)return b;
    const r=Math.round(pa[0]+(pb[0]-pa[0])*t),g=Math.round(pa[1]+(pb[1]-pa[1])*t),bl=Math.round(pa[2]+(pb[2]-pa[2])*t);
    return`rgb(${r},${g},${bl})`
  }
  function hexToRgb(hex){
    const m=/^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return m?[parseInt(m[1],16),parseInt(m[2],16),parseInt(m[3],16)]:null
  }

  function selectCategory(key,button){
    const isSame=current.key===key;
    catContainer.querySelectorAll('[data-tsc-cat]').forEach(btn=>{
      const selected=!isSame&&btn===button;
      btn.setAttribute('aria-pressed',String(selected));
      btn.classList.toggle('selected',selected)
    });
    animateTo(isSame?config.main:config.categories.find(c=>c.key===key))
  }

  if(catContainer){
    catContainer.querySelectorAll('[data-tsc-cat]').forEach(button=>{
      button.addEventListener('click',()=>selectCategory(button.dataset.tscCat,button));
    })
  }

  function showTooltip(clientX){
    const rect=svg.getBoundingClientRect(),scaleX=W/rect.width;
    const localX=(clientX-rect.left)*scaleX;
    const n=current.points.length,geo=seriesGeometry(current);
    let nearest=0,best=Infinity;
    geo.xs.forEach((x,i)=>{const dist=Math.abs(x-localX);if(dist<best){best=dist;nearest=i}});
    const px=geo.xs[nearest],py=geo.ys[nearest];
    focusGroup.hidden=false;
    focusLine.setAttribute('x1',px);focusLine.setAttribute('x2',px);focusLine.setAttribute('y1',PAD.top);focusLine.setAttribute('y2',PAD.top+PLOT_H);
    focusDot.setAttribute('cx',px);focusDot.setAttribute('cy',py);
    const periodLabel=config.periods[Math.min(Math.round(nearest/(n-1)*(config.periods.length-1)),config.periods.length-1)];
    tooltip.hidden=false;
    tooltip.textContent=`${periodLabel}: ${fmtNumber(current.points[nearest])} ${config.unit||''}`;
    const ratioX=px/W,wrapRect=container.querySelector('.tsc-chart-wrap').getBoundingClientRect();
    tooltip.style.left=`${ratioX*wrapRect.width}px`;
    tooltip.style.top=`${(py/H)*wrapRect.height-14}px`
  }
  function hideTooltip(){focusGroup.hidden=true;tooltip.hidden=true}
  hit.addEventListener('pointermove',e=>showTooltip(e.clientX));
  hit.addEventListener('pointerleave',hideTooltip);
  hit.addEventListener('pointerdown',e=>showTooltip(e.clientX));

  setAria(config.main)
}

function mountAll(scope){
  (scope||document).querySelectorAll('[data-tsc]').forEach(container=>{
    const config=registry.get(container.dataset.tscId);
    if(config)mount(container,config)
  })
}

window.TimeSeriesChart={render,mountAll};
})();
