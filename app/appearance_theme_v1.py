"""Small client-side appearance control. No server writes or app reruns.

System appearance is the default until an explicit browser-local preference
is saved. A single delegated listener handles both the mobile Menu's owl
switch and the optional Settings appearance switch.
The preference is local to the browser and is never sent to application services.
"""
from __future__ import annotations

STORAGE_KEY = "munshi.appearance.v1"

OWL = (
    '<svg viewBox="0 0 36 36" fill="none" stroke="currentColor" '
    'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
    'aria-hidden="true" focusable="false">'
    '<path d="M6 12 5 4l8 5M30 12l1-8-8 5"/>'
    '<path d="M7 12c2-5 7-7 11-7s9 2 11 7c2 7 0 14-6 18l-5 2-5-2C7 26 5 19 7 12Z"/>'
    '<path d="m15 23 3 3 3-3-3-2Z"/>'
    '<path d="M7 23c2 1 4 1 6 1M29 23c-2 1-4 1-6 1"/>'
    '<g class="munshi-owl-eye"><ellipse cx="13.4" cy="16.8" rx="4.2" ry="4.6" '
    'fill="currentColor" stroke="none"/><circle cx="13.8" cy="16.7" r="1.6" '
    'fill="#F4F4F0" stroke="none"/></g>'
    '<g class="munshi-owl-eye"><ellipse cx="22.6" cy="16.8" rx="4.2" ry="4.6" '
    'fill="currentColor" stroke="none"/><circle cx="22.2" cy="16.7" r="1.6" '
    'fill="#F4F4F0" stroke="none"/></g>'
    '</svg>'
)


def appearance_toggle_html(*, settings: bool = False) -> str:
    """Original decorative owl and a real accessible Light/Dark switch."""
    wrapper = "munshi-appearance-settings" if settings else "munshi-appearance-menu"
    return (
        f'<div class="{wrapper}"><div class="munshi-appearance-row">'
        '<button class="munshi-theme-switch" data-munshi-toggle type="button" '
        'role="switch" aria-checked="false" aria-label="Dark mode">'
        '<span class="munshi-owl">' + OWL + '</span>'
        '<span class="munshi-theme-copy"><strong>Dark mode</strong>'
        '<span class="munshi-theme-hint">Graphite &amp; Platinum</span></span>'
        '<span class="munshi-switch-track" aria-hidden="true">'
        '<span class="munshi-switch-knob"></span></span>'
        '</button></div></div>'
    )


def early_theme_boot() -> str:
    """Resolve saved preference first, otherwise system appearance, pre-paint."""
    return (
        "<script>(()=>{"
        "let dark=false;"
        "try{dark=!!window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches}catch(_){}"
        "let theme=dark?'dark':'light';"
        "try{const saved=window.localStorage.getItem('munshi.appearance.v1');"
        "if(saved==='dark'||saved==='light')theme=saved}catch(_){}"
        "document.documentElement.dataset.munshiTheme=theme;"
        "})();</script>"
    )


def appearance_controller_html() -> str:
    """Static JS only, installed once per browser document, with cheap DOM updates."""
    return r"""<script>
(()=>{
 const root=document.documentElement;
 const key='munshi.appearance.v1';
 if(window.__munshiAppearanceInstalled)return;
 window.__munshiAppearanceInstalled=true;
 const isDark=()=>root.dataset.munshiTheme==='dark';
 const syncControls=()=>{
   const checked=isDark()?'true':'false';
   for(const el of document.querySelectorAll('[data-munshi-toggle]')){
     if(el.getAttribute('aria-checked')!==checked)el.setAttribute('aria-checked',checked);
   }
 };
 const apply=(dark)=>{
   root.dataset.munshiTheme=dark?'dark':'light';
   try{window.localStorage.setItem(key,dark?'dark':'light')}catch(_){}
   syncControls();
 };
 const system=typeof window.matchMedia==='function'?
   window.matchMedia('(prefers-color-scheme: dark)'):null;
 const preferredTheme=()=>{
   let theme=system?.matches?'dark':'light';
   try{
     const saved=window.localStorage.getItem(key);
     if(saved==='dark'||saved==='light')theme=saved;
   }catch(_){}
   return theme;
 };
 root.dataset.munshiTheme=preferredTheme();
 // Follow OS appearance changes only when the user has not selected a theme.
 system?.addEventListener?.('change',()=>{
   root.dataset.munshiTheme=preferredTheme();
   syncControls();
 });
 document.addEventListener('click',(event)=>{
   const button=event.target instanceof Element?
     event.target.closest('[data-munshi-toggle]'):null;
   if(button){
     event.preventDefault();
     apply(!isDark());
     return;
   }
   if(event.target instanceof Element &&
      event.target.closest('.mobile-nav summary'))requestAnimationFrame(syncControls);
 },false);
 document.addEventListener('focusin',(event)=>{
   if(event.target instanceof Element && event.target.matches('[data-munshi-toggle]')){
     syncControls();
   }
 },false);
 // Streamlit replaces page fragments. Keep ARIA in sync when a fresh menu
 // appears, without scanning job grids or changing application state.
 let pending=false;
 new MutationObserver((changes)=>{
   for(const change of changes){
     if(change.type!=='childList')continue;
     for(const node of change.addedNodes){
       if(node.nodeType!==1)continue;
       if(node.matches?.('[data-munshi-toggle]')||
          node.querySelector?.('[data-munshi-toggle]')){
         if(!pending){
           pending=true;
           requestAnimationFrame(()=>{pending=false;syncControls()});
         }
         return;
       }
     }
   }
 }).observe(document.documentElement,{childList:true,subtree:true});
 syncControls();
})();
</script>"""
