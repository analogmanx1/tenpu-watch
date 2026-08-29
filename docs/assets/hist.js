// 自動生成: build_site.py — 検索履歴(このブラウザ内のlocalStorageにだけ保存。どこにも送信しない)
(function(){
window.searchHistory=function(opts){
  var QK=opts.key+':qh', OK=opts.key+':oh', QMAX=10, OMAX=15;
  var box=opts.box;
  function load(k){try{var v=JSON.parse(localStorage.getItem(k));return Array.isArray(v)?v:[];}catch(e){return[];}}
  function save(k,v){try{localStorage.setItem(k,JSON.stringify(v));}catch(e){}}
  function esc(s){return (s||'').replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function cut(s,n){return s.length>n?s.slice(0,n)+'…':s;}
  function addQuery(w){ if(!w)return; var a=load(QK).filter(function(x){return x!==w;}); a.unshift(w); save(QK,a.slice(0,QMAX)); }
  function addOpen(label,url){ if(!url)return; var a=load(OK).filter(function(x){return x.u!==url;}); a.unshift({t:label||url,u:url}); save(OK,a.slice(0,OMAX)); }
  function render(){
    var qs=load(QK), os=load(OK);
    if(!qs.length&&!os.length){ box.innerHTML=''; return; }
    var h='';
    if(qs.length){ h+='<div class="histhead">🕘 最近の検索</div><div class="chips">'+qs.map(function(w){
      return '<span class="chip" data-q="'+esc(w)+'" title="クリックで再検索">'+esc(w)+'<span class="x" title="この履歴を消す" data-xq="'+esc(w)+'">✕</span></span>';
    }).join('')+'</div>'; }
    if(os.length){ h+='<div class="histhead">📂 最近開いたPDF</div><div class="chips">'+os.map(function(o){
      return '<span class="chip"><a href="'+esc(o.u)+'" target="_blank" rel="noopener" title="'+esc(o.t)+'">'+esc(cut(o.t,28))+'</a><span class="x" title="この履歴を消す" data-xo="'+esc(o.u)+'">✕</span></span>';
    }).join('')+'</div>'; }
    h+='<p class="small"><a href="javascript:void(0)" id="histclear">履歴を全部消す</a> ｜ 履歴はこのブラウザ内にだけ保存されます(PC・ブラウザごとに別)</p>';
    box.innerHTML=h;
  }
  box.addEventListener('click',function(ev){
    var t=ev.target;
    if(t.id==='histclear'){ save(QK,[]); save(OK,[]); render(); return; }
    if(t.dataset&&t.dataset.xq){ save(QK,load(QK).filter(function(x){return x!==t.dataset.xq;})); render(); return; }
    if(t.dataset&&t.dataset.xo){ save(OK,load(OK).filter(function(x){return x.u!==t.dataset.xo;})); render(); return; }
    var chip=t.closest('.chip');
    if(chip&&chip.dataset.q&&!t.closest('a')){ opts.rerun(chip.dataset.q); }
  });
  return {addQuery:addQuery, addOpen:addOpen,
          show:function(){ box.style.display=''; render(); },
          hide:function(){ box.style.display='none'; }};
};
})();
