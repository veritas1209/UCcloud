/* No service or VM password is persisted in browser storage. */
(() => {
  const $ = id => document.getElementById(id);
  const auto = new URLSearchParams(location.search).get('software') === '1';
  const button = document.createElement('button');
  button.id='software-add';button.type='button';button.textContent='프로그램 추가';button.disabled=true;
  document.querySelector('.heading-actions').append(button);
  const dialog=document.createElement('dialog');dialog.id='software-add-dialog';
  dialog.setAttribute('aria-labelledby','software-add-title');
  dialog.innerHTML='<form id="software-add-form"><div class="software-add-head"><h2 id="software-add-title">프로그램 추가</h2><button id="software-add-close" type="button">닫기</button></div><p class="help">새 프로그램만 추가합니다. 기존 프로그램과 파일은 바꾸지 않습니다.</p><label for="software-add-select">프로그램</label><select id="software-add-select"><option value="">불러오는 중…</option></select><div id="software-add-list"></div><p id="software-add-message" role="status" aria-live="polite"></p><button id="software-add-start" type="submit" disabled>설치하기</button></form>';
  document.body.append(dialog);
  let send=null,catalog=[],selected=[],request=null,opened=false,timer=null;
  const escape=value=>String(value).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  function message(text){$('software-add-message').textContent=text;}
  function clear(){selected.forEach(item=>{item.password='';});selected=[];$('software-add-list').replaceChildren();}
  function render(){
    $('software-add-list').innerHTML=selected.map(item=>{
      const spec=catalog.find(value=>value.id===item.id);
      return `<fieldset data-program="${escape(item.id)}"><legend>${escape(spec.name)}</legend><label>버전<select data-field="version">${spec.versions.map(version=>`<option ${version===item.version?'selected':''}>${escape(version)}</option>`).join('')}</select></label>${spec.port?`<label>포트<input data-field="port" type="number" min="1024" max="65535" value="${item.port}" required></label>`:''}${spec.credentials?`<label>서비스 ID<input data-field="username" value="${escape(item.username)}" pattern="[a-z][a-z0-9_]{1,31}" minlength="2" maxlength="32" required autocomplete="off"></label><label>서비스 비밀번호 · 8자 이상<input data-field="password" type="password" minlength="8" maxlength="128" required autocomplete="new-password"></label>`:''}<button data-remove="${escape(item.id)}" type="button">빼기</button></fieldset>`;
    }).join('');
    dialog.querySelectorAll('input[data-field=password]').forEach(input=>{input.value=selected.find(item=>item.id===input.closest('fieldset').dataset.program)?.password||'';});
    $('software-add-start').disabled=!send||!selected.length||Boolean(request);
  }
  async function open(){
    if(dialog.open)return;
    dialog.showModal();opened=true;message('설치할 프로그램을 선택하세요.');
    try{
      const response=await fetch('/api/catalog',{credentials:'same-origin',cache:'no-store'});
      if(!response.ok)throw new Error();
      catalog=(await response.json()).software||[];
      $('software-add-select').innerHTML='<option value="">프로그램 선택</option>'+catalog.map(item=>`<option value="${escape(item.id)}">${escape(item.name)}</option>`).join('');
      if(!catalog.length)message('프로그램 설치를 준비 중입니다. 운영자에게 문의해 주세요.');
    }catch{message('프로그램 목록을 불러오지 못했습니다. 잠시 후 다시 열어 주세요.');}
    render();
  }
  button.addEventListener('click',()=>void open());
  $('software-add-close').addEventListener('click',()=>{clear();dialog.close();});
  dialog.addEventListener('close',clear);
  $('software-add-select').addEventListener('change',event=>{
    const spec=catalog.find(item=>item.id===event.target.value);
    if(!spec||selected.some(item=>item.id===spec.id))return;
    selected.push({id:spec.id,version:spec.versions[0],...(spec.port?{port:spec.port}:{}),...(spec.credentials?{username:'student',password:''}:{})});render();
  });
  $('software-add-list').addEventListener('input',event=>{
    const field=event.target.dataset.field;const item=selected.find(item=>item.id===event.target.closest('fieldset')?.dataset.program);
    if(item&&field)item[field]=field==='port'?Number(event.target.value):event.target.value;
  });
  $('software-add-list').addEventListener('click',event=>{
    const id=event.target.closest('[data-remove]')?.dataset.remove;
    if(id){selected=selected.filter(item=>item.id!==id);render();}
  });
  $('software-add-form').addEventListener('submit',event=>{
    event.preventDefault();if(!send||request||!selected.length)return;
    request=crypto.randomUUID();$('software-add-start').disabled=true;message('VM에 설치를 요청하고 있습니다.');
    timer=setTimeout(()=>{request=null;render();message('요청 결과를 확인하지 못했습니다. 다시 접속해 설치 상태를 먼저 확인하세요.');},55000);
    try{send({type:'software.install',request_id:request,selections:selected.map(item=>({...item}))});}
    catch{clearTimeout(timer);request=null;render();message('VM에 다시 접속해 주세요.');}
  });
  window.uccloudSoftware={
    connected(sender){send=sender;button.disabled=false;if(auto&&!opened)void open();},
    disconnected(){send=null;button.disabled=true;clearTimeout(timer);request=null;clear();if(dialog.open)message('VM에 다시 접속해 주세요.');},
    receive(item){
      if(item.type==='software.install'&&item.request_id===request){clearTimeout(timer);request=null;message(item.message);if(item.state==='accepted')clear();render();}
      if(item.type==='software'&&dialog.open)message(item.message);
    },
  };
  window.addEventListener('pagehide',()=>{clear();clearTimeout(timer);});
})();
