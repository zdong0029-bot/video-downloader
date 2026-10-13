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
 panel.className='admin-board';panel.removeAttribute('style');panel.replaceChildren(cloud,platformStack,taskSummary,cleanup,service,userCard);
 function navButton(text,key,id){const b=id?q('#'+id):make('button');b.textContent=text;b.type='button';b.dataset.adminSection=key;b.onclick=()=>window.openAdmin(key);adminNav.append(b);return b;}
 navButton('管理概览','overview');navButton('网盘账号','cloud','cloudAccountsBtn');navButton('平台授权','platforms');navButton('全部任务','tasks','scopeBtn');navButton('服务检查','services');navButton('管理员账号','users','adminBtn');
 const visitorNote=make('p','sidebar-help','原文件下载 · 最高可用画质');
 foot.append(visitorNote,q('#loginBtn'),q('#logoutBtn'));
 top.remove();content.append(header,subnav,panel,download);main.replaceChildren(aside,content);
 q('#videoTab').onclick=()=>window.openDownload('video');q('#cloudTab').onclick=()=>window.openDownload('cloud');q('#historyTab').onclick=()=>window.openDownload(null,'history');
 const sectionNames={overview:'管理概览',cloud:'网盘账号',platforms:'平台授权',tasks:'全部任务',services:'服务检查',users:'管理员账号'};
 function layout(){
   main.dataset.layout=mode;main.dataset.adminSection=section;
   visitorNav.hidden=mode==='admin';adminNav.hidden=mode!=='admin';
   panel.hidden=mode!=='admin'||section==='tasks';download.hidden=mode==='admin'&&section!=='tasks';
   modeButton.hidden=!ME;modeButton.textContent=mode==='admin'?'返回下载页面':'管理后台';
   label.textContent=mode==='admin'?'管理后台':'下载工作区';subnav.hidden=mode==='admin'||VIEW==='history'||CATEGORY==='cloud';
   q('#logoutBtn').hidden=!ME;q('#loginBtn').hidden=!!ME;
   q('#whoName').hidden=!ME;q('#scopeBtn').textContent='全部任务';
   for(const b of adminNav.querySelectorAll('button'))b.setAttribute('aria-current',b.dataset.adminSection===section?'page':'false');
   const visible={overview:[cloud,platformStack,taskSummary,cleanup],cloud:[cloud,cleanup],platforms:[platformStack],services:[service],users:[userCard]}[section]||[];
   for(const child of panel.children)child.hidden=!visible.includes(child);
   if(mode==='admin'){
     title.textContent=sectionNames[section];description.textContent=section==='overview'?'集中管理后台账号、平台授权和下载任务':'后台管理仅对管理员开放';
     if(section==='tasks'){q('#downloadForm').hidden=true;q('#workspaceTitle').textContent='全部下载记录';q('#workspaceNote').textContent='按来源分组 · 可搜索和筛选所有访客的任务';}
   }else{
     title.textContent=VIEW==='history'?'下载记录':CATEGORY==='cloud'?'网盘下载':'视频 / 图片';
     description.textContent=VIEW==='history'?'任务结束后保留七天，记录和文件分别管理。':'粘贴链接，整理素材，选择后下载。';
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
 };
 window.openDownload=function(category=null,view='workspace'){
   mode='download';SCOPE='browser';
   if(category)setCategory(category);else setView(view);
   layout();void refresh(true);if(typeof refreshCatalogs==='function')void refreshCatalogs(true);
 };
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
