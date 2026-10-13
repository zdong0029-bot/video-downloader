// A display group preserves each file's real job ID for download and removal.
const expandedCards = new Set();

function rememberExpanded(){}
function originId(job,file){return file.job_id || job.id;}
function viewJobs(jobs){
  const groups=new Map(),out=[];
  for(const j of jobs){
    const single=j.batch_id && j.kind!=='collection' && j.kind!=='cloud' && j.kind!=='file'
      && j.total===1 && (j.files||[]).length<=1 && (!(j.files||[]).length || /\.(mp4|webm|mkv|mov|m4v|avi)$/i.test(j.files[0].name))
      && (j.kind==='douyin' || !['queued','running'].includes(j.status));
    if(!single){out.push(j);continue;}
    const id='batch-'+j.batch_id;
    if(!groups.has(id)){const group={id,is_group:true,members:[],member_ids:[],files:[],created:j.created};groups.set(id,group);out.push(group);}
    const g=groups.get(id);g.members.push(j);g.member_ids.push(j.id);
    g.files.push(...(j.files||[]).map(f=>({...f,job_id:j.id,source_url:j.url})));
    g.created=Math.max(g.created,j.created);
  }
  for(const g of groups.values()){
    const m=g.members;g.total=m.length;g.finished=m.filter(j=>j.status==='done').length;
    g.failed=m.filter(j=>j.status==='error').length;
    const running=m.some(j=>j.status==='running'),queued=m.some(j=>j.status==='queued');
    g.status=running?'running':queued?'queued':g.failed?(g.files.length?'partial':'error'):m.some(j=>j.status==='cancelled')?'cancelled':'done';
    g.progress=m.reduce((n,j)=>n+(j.status==='done'?1:j.progress||0),0)/m.length;
    g.title='单视频批量 · '+m.length+' 个链接';
    g.url='同一次提交的单视频链接，统一勾选和下载';
    g.owner=m[0].owner;
    const active=m.filter(j=>['running','queued'].includes(j.status)).length;
    g.message='已准备 '+g.files.length+' / '+m.length+' 个视频'+(active?'，'+active+' 个处理中':'')+(g.failed?'，'+g.failed+' 个失败':'');
  }
  return out.sort((a,b)=>b.created-a.created);
}
function viewJob(id){return viewJobs(cache).find(j=>j.id===id) || cache.find(j=>j.id===id);}
function toggleExpanded(id){
  expandedCards.has(id)?expandedCards.delete(id):expandedCards.add(id);
  rememberExpanded();repaint();
}


const fileVisible=new Map();
function showMoreFiles(id){fileVisible.set(id,(fileVisible.get(id)||60)+100);repaint();}
