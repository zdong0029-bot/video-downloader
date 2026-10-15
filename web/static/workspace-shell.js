/* Two role workspaces share existing download and authorization controls. */
(() => {
 const q=s=>document.querySelector(s);
 const make=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls||'';if(text)n.textContent=text;return n;};
 let mode='download',section='overview',role=null;
 const main=q('#main'),top=q('.topbar'),panel=q('#adminPanel');
 const aside=make('aside','app-sidebar');aside.setAttribute('aria-label','工作台导航');
 const brand=make('div','sidebar-brand');brand.append(top.querySelector('h1'));
 const visitorNav=q('.category-tabs');visitorNav.classList.add('sidebar-nav');
 const subnav=make('div','video-subnav');subnav.setAttribute('aria-label','视频提取方式');
 const mediaTab=make('button','','视频图片提取');mediaTab.id='mediaExtractTab';mediaTab.type='button';mediaTab.onclick=()=>window.openDownload('video');
 const profileTab=make('button','','主页 / 合集');profileTab.id='profileExtractTab';profileTab.type='button';profileTab.onclick=()=>window.openDownload('profile');
 subnav.append(mediaTab,profileTab);q('#cloudTab').textContent='网盘下载';
 const toolBar=q('#toolBar');if(toolBar){toolBar.className='sidebar-tools';toolBar.setAttribute('aria-label','批量工具');q('#videoTab').after(toolBar);}
 const adminNav=make('nav' ,'sidebar-nav admin-navigation');adminNav.setAttribute('aria-label','管理导航');
 const foot=make('div','sidebar-footer');
 const label=make('div','sidebar-label','工作空间');
 aside.append(brand,label,visitorNav,adminNav,foot);
 const content=make('div','app-content');
 const header=make('header','app-header');const headings=make('div');
 const title=make('h2','app-title'),description=make('p','app-description');headings.append(title,description);
 const identity=make('div','header-identity');identity.append(q('#whoName'));
 const modeButton=make('button','ghost','管理后台');modeButton.id='modeButton';modeButton.type='button';
 modeButton.onclick=()=>mode==='admin'?window.openDownload():window.openAdmin('overview');
 identity.append(modeButton);header.append(headings,identity);
 const download=make('section','download-workspace');download.id='downloadWorkspace';
 let n=q('#downloadForm');while(n){const next=n.nextElementSibling;download.append(n);n=next;}
 const userCard=make('section','admin-module');userCard.id='adminUsers';
 userCard.append(panel.querySelector('h3'),q('#userList'),q('#adminMsg'));
 const cloud=q('#cloudAccountSection');cloud.className='admin-module';cloud.removeAttribute('style');
 const cleanup=make('section','admin-module');cleanup.id='adminCleanup';
 const cleanupTitle=cloud.querySelector('h4');const cleanupNote=cleanupTitle.nextElementSibling;
 cleanup.append(cleanupTitle,cleanupNote,q('#cloudTransfers'));
 const channels=q('#channelStatus').closest('section');channels.className='admin-module';channels.removeAttribute('style');channels.id='channelAccountSection';
 const platforms=q('#platformAccountSection');platforms.className='admin-module';platforms.removeAttribute('style');
 const platformStack=make('div','platform-stack');platformStack.id='adminPlatforms';platformStack.append(platforms,channels);
 const service=make('section','admin-module');service.id='adminServices';
 service.append(make('h3','','服务与访问'),make('p','note','朋友使用公网网址；127.0.0.1 只在运行下载器的这台电脑有效。'));
 const address=make('a','service-address',PUBLIC_SITE);address.href=PUBLIC_SITE;address.target='_blank';address.rel='noopener';service.append(address);
 const serviceActions=make('div','service-actions');serviceActions.append(q('#shareSiteBtn'),q('#connectionChecker'));
 for(const a of channels.querySelectorAll('a'))serviceActions.append(a);
 service.append(serviceActions);
 const taskSummary=make('section','admin-module');taskSummary.id='adminTaskSummary';taskSummary.append(make('h3','','近期任务'));
 const summaryRows=make('div','summary-rows');taskSummary.append(summaryRows);
 const all=make('button','ghost','查看全部任务 →');all.onclick=()=>window.openAdmin('tasks');taskSummary.append(all);
 const statsCard=make('section','admin-module stats-module');statsCard.id='adminStats';
 statsCard.append(make('h3','','今日数据'));const statsBody=make('div','stats-body');statsBody.append(make('p','note','正在读取…'));statsCard.append(statsBody);
 panel.className='admin-board';panel.removeAttribute('style');panel.replaceChildren(statsCard,cloud,platformStack,taskSummary,cleanup,service,userCard);
 function navButton(text,key,id){const b=id?q('#'+id):make('button');b.textContent=text;b.type='button';b.dataset.adminSection=key;b.onclick=()=>window.openAdmin(key);adminNav.append(b);return b;}
 navButton('管理概览','overview');navButton('访问统计','stats');navButton('网盘账号','cloud','cloudAccountsBtn');navButton('平台授权','platforms');navButton('全部任务','tasks','scopeBtn');navButton('服务检查','services');navButton('管理员账号','users','adminBtn');
 const visitorNote=make('p','sidebar-help','原文件下载 · 最高可用画质');
 foot.append(visitorNote,q('#loginBtn'),q('#logoutBtn'));
 const toolView=make('section','tool-view');toolView.id='toolView';toolView.hidden=true;
 const toolFrame=document.createElement('iframe');toolFrame.title='批量工具';toolFrame.className='tool-frame';toolView.append(toolFrame);
 let TOOL='';const TOOL_TITLES={trim:'批量剪辑',convert:'格式转换',md5:'修改 MD5',audio:'视频转音频'};
 window.openToolView=function(op,items=[]){
   mode='download';TOOL=op;
   const w=toolFrame.contentWindow;
   if(toolFrame.dataset.ready==='1'&&w&&typeof w.setOp==='function'){w.setOp(op);if(items.length)w.addServer(items);}
   else{try{localStorage.setItem('vd-tool-items',JSON.stringify({t:Date.now(),items}));}catch{}toolFrame.dataset.ready='0';toolFrame.src='/tools.html?embed=1&op='+op;
     toolFrame.onload=()=>{toolFrame.dataset.ready='1';};}
   layout();
 };
 window.addEventListener('message',e=>{if(e.origin===location.origin&&e.data&&e.data.toolHeight)toolFrame.style.height=Math.max(500,e.data.toolHeight)+'px';});
 top.remove();content.append(header,subnav,panel,download,toolView);main.replaceChildren(aside,content);
 q('#videoTab').onclick=()=>window.openDownload('video');q('#cloudTab').onclick=()=>window.openDownload('cloud');q('#historyTab').onclick=()=>window.openDownload(null,'history');
 const sectionNames={stats:'访问统计',overview:'管理概览',cloud:'网盘账号',platforms:'平台授权',tasks:'全部任务',services:'服务检查',users:'管理员账号'};
 function layout(){
   main.dataset.layout=mode;main.dataset.adminSection=section;
   visitorNav.hidden=mode==='admin';adminNav.hidden=mode!=='admin';
   panel.hidden=mode!=='admin'||section==='tasks';download.hidden=mode==='admin'&&section!=='tasks';
   modeButton.hidden=!ME;modeButton.textContent=mode==='admin'?'返回下载页面':'管理后台';
   label.textContent=mode==='admin'?'管理后台':'下载工作区';subnav.hidden=mode==='admin'||VIEW==='history'||CATEGORY==='cloud';
   q('#logoutBtn').hidden=!ME;q('#loginBtn').hidden=!!ME;
   q('#whoName').hidden=!ME;q('#scopeBtn').textContent='全部任务';
   for(const b of adminNav.querySelectorAll('button'))b.setAttribute('aria-current',b.dataset.adminSection===section?'page':'false');
   const inTool=mode!=='admin'&&!!TOOL;toolView.hidden=!inTool;if(inTool){download.hidden=true;subnav.hidden=true;}
   main.dataset.tool=inTool?TOOL:'';
   for(const b of document.querySelectorAll('#toolBar button[data-tool]'))b.setAttribute('aria-current',inTool&&b.dataset.tool===TOOL?'page':'false');
   const visible={stats:[statsCard],overview:[statsCard,cloud,platformStack,taskSummary,cleanup],cloud:[cloud,cleanup],platforms:[platformStack],services:[service],users:[userCard]}[section]||[];
   for(const child of panel.children)child.hidden=!visible.includes(child);
   if(mode==='admin'){
     title.textContent=sectionNames[section];description.textContent=section==='stats'?'每天的访客和下载量，按本机日期统计':section==='overview'?'集中管理后台账号、平台授权和下载任务':'后台管理仅对管理员开放';
     if(section==='tasks'){q('#downloadForm').hidden=true;q('#workspaceTitle').textContent='全部下载记录';q('#workspaceNote').textContent='按来源分组 · 可搜索和筛选所有访客的任务';}
   }else{
     title.textContent=inTool?'批量工具 · '+TOOL_TITLES[TOOL]:VIEW==='history'?'下载记录':CATEGORY==='cloud'?'网盘下载':'视频 / 图片';
     description.textContent=inTool?'上传本地视频或用下载列表里的视频，一次处理一批。':VIEW==='history'?'任务结束后保留七天，记录和文件分别管理。':'粘贴链接，整理素材，选择后下载。';
   }
 }
 window.openAdmin=function(target='overview'){
   if(!ME){showGate();return;}
   mode='admin';section=target;SCOPE='all';void refresh(true);
   if(target==='tasks'){SCOPE='all';setView('history');}
   layout();
   if(['overview','cloud'].includes(target))window.loadCloudAccounts?.();
   if(['overview','platforms'].includes(target))window.refreshVideoAccounts?.();
   if(target==='users')void loadUsers();
   renderSummary();
   if(['overview','stats'].includes(target))void renderStats(target==='stats');
 };
 window.openDownload=function(category=null,view='workspace'){
   mode='download';SCOPE='browser';TOOL='';
   if(category)setCategory(category);else setView(view);
   layout();void refresh(true);if(typeof refreshCatalogs==='function')void refreshCatalogs(true);
 };
 const fmtBytes=n=>n>=1073741824?(n/1073741824).toFixed(2)+' GB':n>=1048576?(n/1048576).toFixed(1)+' MB':n>0?Math.max(1,Math.round(n/1024))+' KB':'0 B';
 async function renderStats(full){
   if(!ME)return;
   let d;try{const r=await fetch('/api/admin/stats?days=14',{cache:'no-store'});if(!r.ok)throw Error();d=await r.json();}catch{statsBody.replaceChildren(make('p','note','统计读取失败，稍后再试。'));return;}
   const t=d.today,y=d.yesterday||{};
   const diff=(a,b)=>b==null?'':a===b?'与昨天持平':(a>b?'比昨天 +':'比昨天 ')+(a-b);
   const tile=(label,value,sub,extra)=>'<div class="stat-tile"><span>'+label+'</span><b>'+value+'</b><small>'+sub+'</small>'+(extra?'<small class="stat-diff">'+extra+'</small>':'')+'</div>';
   const dev=[t.desktop?'电脑 '+t.desktop:'',t.mobile?'手机 '+t.mobile:'',t.tablet?'平板 '+t.tablet:''].filter(Boolean).join(' · ')||'还没有访客';
   let html='<div class="stat-grid">'
     +tile('今日访客',t.visitors,dev,diff(t.visitors,y.visitors))
     +tile('新访客',t.new,'第一次来的浏览器',diff(t.new,y.new))
     +tile('当前在线',d.online,'近 5 分钟有操作','累计 '+d.all_time_devices+' 个浏览器')
     +tile('视频下载',(t.video_files+t.profile_files)+' 个文件','提交 '+(t.video_links+t.profile_links)+' 个链接 · '+fmtBytes(t.video_bytes+t.profile_bytes),diff(t.video_files+t.profile_files,(y.video_files||0)+(y.profile_files||0)))
     +tile('网盘下载',t.cloud_files+' 个文件','提交 '+t.cloud_links+' 个链接 · '+fmtBytes(t.cloud_bytes),diff(t.cloud_files,y.cloud_files))
     +tile('批量工具',t.tool_links+' 次','产出 '+t.tool_files+' 个文件',diff(t.tool_links,y.tool_links))
     +'</div>';
   if(full){
     html+='<table class="stat-table"><thead><tr><th>日期</th><th>访客</th><th>新访客</th><th>视频文件</th><th>网盘文件</th><th>工具</th><th>下载量</th></tr></thead><tbody>'
       +d.days.map(r=>'<tr><td>'+r.day.slice(5)+'</td><td>'+r.visitors+'</td><td>'+r.new+'</td><td>'+(r.video_files+r.profile_files)+'</td><td>'+r.cloud_files+'</td><td>'+r.tool_links+'</td><td>'+fmtBytes(r.video_bytes+r.profile_bytes+r.cloud_bytes)+'</td></tr>').join('')
       +'</tbody></table>';
   }
   html+='<p class="note stat-rule">'+d.rule+(t.admin?' 今天管理员浏览器 '+t.admin+' 个。':'')+(full?'':' <a href="#" onclick="openAdmin(\'stats\');return false">查看近 14 天 →</a>')+'</p>';
   statsBody.innerHTML=html;
 }
 setInterval(()=>{if(mode==='admin'&&['overview','stats'].includes(section)&&!document.hidden)void renderStats(section==='stats');},30000);
 function renderSummary(){
   if(!ME)return;summaryRows.replaceChildren();
   const recent=(typeof cache!=='undefined'?cache:[]).slice(0,4);
   if(!recent.length){summaryRows.append(make('p','note','当前没有可显示的任务。'));return;}
   for(const j of recent){const row=make('div','summary-row');row.append(make('span','',j.title||j.url||j.id),make('span','badge',({queued:'排队中',running:'下载中',done:'已完成',error:'失败',cancelled:'已取消'}[j.status]||j.status)));summaryRows.append(row);}
 }
 window.applyRoleLayout=function(){
   const nextRole=!!ME;
   if(role!==nextRole){role=nextRole;mode=ME?'admin':'download';section='overview';}
   layout();
   if(ME&&mode==='admin')window.openAdmin(section);
 };
 setInterval(renderSummary,2500);
 if(!main.hidden)window.applyRoleLayout();
})();
