"use strict";
/* Course-first read views. Mutations continue through the established administrator APIs. */
let coursesSequence=0;
const courseError=e=>e.net?"Կապը սերվերի հետ չհաջողվեց։ Փորձեք կրկին։":e.message==='auth'?"Խնդրում ենք կրկին մուտք գործել։":e.message;
function courseAvatarStack(team){const people=[...(team?.lecturers||[]),...(team?.tas||[])];return `<div class="course-avatars" aria-label="Խմբի թիմը">${[...new Map(people.map(p=>[p.id,p])).values()].slice(0,6).map(p=>`<span class="gp-avatar" title="${esc(p.name)}">${p.avatar?`<img src="${esc(p.avatar)}" alt="">`:esc(p.name.trim().split(/\s+/).slice(0,2).map(x=>x[0]).join(''))}</span>`).join('')}</div>`;}
async function renderGroupsPage(force=false){
  const stable=$("#main .courses-page");if(!force&&stable?.dataset.courseRoute===location.hash)return;
  const seq=++coursesSequence;groupsStyles();
  const match=location.hash.match(/^#\/groups\/([^?]+)/),name=match?decodeURIComponent(match[1]):null;
  const tab=new URLSearchParams(location.hash.split('?')[1]||'').get('tab')||'general';
  if(name && ['edit','schedule','create'].includes(tab) && S.role==='admin')return renderLegacyGroupsPage({kind:tab==='schedule'?'team':'edit',name:tab==='create'?null:name});
  const main=$('#main');main.innerHTML='<div class="card" role="status">Տվյալները բեռնվում են…</div>';
  try{
    if(!name){
      const data=await api('/api/courses');if(seq!==coursesSequence||!location.hash.startsWith('#/groups'))return;
      const displayCourses=['admin','viewer'].includes(S.role)?data.courses:data.courses.filter(c=>c.groups.length);
      function group(g){return `<a class="course-group" href="#/groups/${encodeURIComponent(g.name)}"><div class="row"><h2 class="grow">${esc(g.short_name)}</h2><span class="chip">${g.completed?'Ավարտված':'Գործող'}</span></div><div class="muted">${esc(groupRange(g.course_start,g.course_end))}</div><div class="muted">${g.subjects.length} առարկա · ${g.counts.students} ուսանող · ${g.counts.lessons} դաս</div>${courseAvatarStack(g.team)}</a>`;}
      main.innerHTML=`<div class="courses-page" data-course-route="${esc(location.hash)}"><div class="top"><div><h1>Դասընթացներ</h1><p>Ընտրեք դասընթացը, ապա՝ ձեր խումբը։</p></div>${S.role==='admin'?'<button class="btn primary" id="course-new">+ Ավելացնել խումբ</button>':''}</div>${displayCourses.map((c,i)=>`<details class="card course-section" ${i===displayCourses.findIndex(x=>x.groups.length)?'open':''}><summary><span class="course-title">${esc(c.name)}</span><span class="pill">${c.groups.length} խումբ</span></summary><div class="course-grid">${c.groups.map(group).join('')||'<p class="course-empty">Խումբ դեռ չկա։</p>'}</div></details>`).join('')}${data.unassigned.length?`<details class="card course-section" open><summary>Չդասակարգված խմբեր</summary><div class="course-grid">${data.unassigned.map(group).join('')}</div></details>`:''}</div>`;
      if($('#course-new'))$('#course-new').onclick=()=>{location.hash='#/groups/new?tab=create';};return;
    }
    const g=await api('/api/group-view/'+encodeURIComponent(name));if(seq!==coursesSequence)return;
    const admin=S.role==='admin',base='#/groups/'+encodeURIComponent(name);
    const tabs=[['general','Ընդհանուր'],['subjects','Առարկաներ'],['team','Թիմ'],...(g.can_students?[['students','Ուսանողներ']]:[]),['lessons','Դասեր']];
    main.innerHTML=`<div class="courses-page" data-course-route="${esc(location.hash)}"><div class="top"><div><a class="account-text-link" href="#/groups">← Դասընթացներ</a><h1>${esc(g.short_name)}</h1>${g.name!==g.short_name?`<p>${esc(g.name)}</p>`:''}</div><span class="chip">${g.completed?'Ավարտված':'Գործող'}</span></div><nav class="course-tabs" aria-label="Խմբի բաժիններ">${tabs.map(([id,label])=>`<a class="chip${id===tab?' on':''}" ${id===tab?'aria-current="page"':''} href="${base}?tab=${id}">${label}</a>`).join('')}</nav><section class="card" id="course-content"></section><p id="course-error" class="err-text" role="alert"></p></div>`;
    const box=$('#course-content');
    if(tab==='subjects'){
      const missing=g.subjects.filter(s=>!s.has_playlist).length;
      box.innerHTML=`<div class="row"><h2 class="grow">Առարկաներ</h2>${admin?`<a class="btn primary" href="${base}?tab=edit">Խմբագրել առարկաները</a>`:''}</div>${missing?`<p class="alert warn">${missing} առարկա առանց տեսացանկի։ ${admin?`<a class="account-text-link" href="${base}?tab=edit">Կապել տեսացանկ</a>`:''}</p>`:''}<div class="course-rows">${g.subjects.map(s=>`<article class="course-row"><div><h3>${esc(s.name)}</h3><p>${esc(s.lecturer?.name||'Դասախոս նշանակված չէ')}</p><span class="muted">${esc(groupRange(s.start_date,s.end_date))}</span></div></article>`).join('')}</div>${admin?`<a class="btn" href="${base}?tab=schedule">Փոխել դասախոսներին և ժամկետները</a>`:''}`;
    }else if(tab==='team'){
      box.innerHTML=groupPeople(g.team)+(admin?`<a class="btn" href="${base}?tab=schedule">Փոխել դասախոսներին</a><h3>Նշանակել ուսանող օգնական</h3><button class="btn" id="course-ta-open">Ընտրել օգնականին</button><div class="course-picker" id="course-picker" hidden><label class="fld">Որոնել անունով<input class="input" id="course-ta-search" type="search" placeholder="Գրեք առնվազն 2 տառ"></label><div id="course-ta-results" aria-live="polite"></div></div>`:'');
      if(admin){$('#course-ta-open').onclick=()=>{$('#course-picker').hidden=false;$('#course-ta-search').focus();};let timer,request=0;
        $('#course-ta-search').oninput=()=>{clearTimeout(timer);const q=$('#course-ta-search').value;const token=++request;timer=setTimeout(async()=>{try{const r=await api('/api/admin/group-students/'+encodeURIComponent(name)+'?q='+encodeURIComponent(q));if(token!==request||!$('#course-ta-results'))return;$('#course-ta-results').innerHTML=r.items.map(p=>`<p class="course-picker-result"><span>${esc(p.name)}</span><button class="btn" data-assign-ta="${p.id}">Նշանակել</button></p>`).join('')||'<p class="muted">Ուսանող չի գտնվել։</p>';document.querySelectorAll('[data-assign-ta]').forEach(b=>b.onclick=()=>saveTas([...g.ta_ids,Number(b.dataset.assignTa)]));}catch(e){$('#course-error').textContent=courseError(e);}},300);};
        box.insertAdjacentHTML('beforeend',g.team.tas.map(p=>`<button class="btn" data-remove-ta="${p.id}">Հեռացնել ${esc(p.name)}</button>`).join(''));
        async function saveTas(ids){try{await api('/api/admin/group-assistants/'+encodeURIComponent(name),{method:'PUT',body:JSON.stringify({ta_ids:[...new Set(ids)]})});await renderGroupsPage(true);}catch(e){$('#course-error').textContent=courseError(e);}}
        box.querySelectorAll('[data-remove-ta]').forEach(b=>b.onclick=()=>saveTas(g.ta_ids.filter(id=>id!==Number(b.dataset.removeTa))));
      }
    }else if(tab==='students' && g.can_students){box.innerHTML=`<h2>Ուսանողներ · ${g.students.length}</h2><div class="course-rows">${g.students.map(p=>`<div class="course-row">${groupPerson(p)}${admin?`<a class="btn" href="#/users/${p.id}">Բացել հաշիվը</a>`:''}</div>`).join('')||'<p class="course-empty">Ուսանողներ դեռ չկան։</p>'}</div>`;
    }else if(tab==='lessons'){
      const selected=g.subjects.find(s=>s.lecturer?.id===S.user?.id)||g.subjects[0];
      box.innerHTML=`<div class="row"><h2 class="grow">Դասեր</h2>${canUpload()&&!g.completed?`<a class="btn primary" href="#/upload?group=${encodeURIComponent(name)}&subject=${encodeURIComponent(selected?.name||'')}">Ավելացնել դաս</a>`:''}</div>${g.lessons.map(l=>`<article class="course-row"><div><h3>${esc(l.title||l.file)}</h3><span class="muted">${esc(l.subject||'')}</span> ${l.demo?'<span class="chip">Ցուցադրական</span>':''}</div><a class="btn" href="${l.demo?'#/library?group='+encodeURIComponent(name):'#/jobs/'+l.id}">Բացել դասը</a></article>`).join('')||'<p class="course-empty">Դասեր դեռ չկան։</p>'}`;
    }else{box.innerHTML=`<h2>Խմբի մասին</h2><p>${esc(groupRange(g.course_start,g.course_end))}</p><div class="course-schedule"><span>${g.counts.students} ուսանող</span><span>${g.subjects.length} առարկա</span><span>${g.counts.lessons} դաս</span></div>${courseAvatarStack(g.team)}${admin?`<p><a class="btn" href="${base}?tab=schedule">Փոխել ժամկետները</a><a class="btn" href="${base}?tab=edit">Խմբի կարգավորումներ</a></p>`:''}`;}
  }catch(e){if(seq===coursesSequence)main.innerHTML=`<div class="alert err" role="alert">${esc(courseError(e))}</div>`;}
}
