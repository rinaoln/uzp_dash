(function(){
  /* Памятка этого отчёта. Своя, а не общая с отчётом по портфелю: у отчётов
     разные разделы, и галочка «больше не показывать» в одном не должна прятать
     инструкцию к другому — поэтому и ключ хранения свой.

     Всё, что трогает localStorage, обёрнуто в try/catch: в приватном окне и при
     запрете данных сайта он бросает исключение, а падать из-за памятки отчёт не
     должен. */
  var KEY = 'uzp.outflow.help.hidden';
  var dlg = document.getElementById('hlp');
  var never = document.getElementById('hlp-never');
  if(!dlg) return;

  function hidden(){
    try { return localStorage.getItem(KEY) === '1'; } catch(e){ return false; }
  }
  function remember(on){
    try { if(on){ localStorage.setItem(KEY, '1'); } else { localStorage.removeItem(KEY); } }
    catch(e){}
  }

  window.hlpOpen = function(){
    if(never) never.checked = hidden();
    if(typeof dlg.showModal === 'function'){ if(!dlg.open) dlg.showModal(); }
    else { dlg.setAttribute('open', ''); }
  };
  window.hlpClose = function(){
    if(typeof dlg.close === 'function'){ dlg.close(); }
    else { dlg.removeAttribute('open'); }
  };
  if(never){ never.addEventListener('change', function(){ remember(never.checked); }); }
  /* клик по подложке закрывает: <dialog> сам этого не делает */
  dlg.addEventListener('click', function(e){ if(e.target === dlg) window.hlpClose(); });

  if(!hidden()) window.hlpOpen();
})();
