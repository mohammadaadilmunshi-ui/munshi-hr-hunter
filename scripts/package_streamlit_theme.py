"""Package first-party presentation into Streamlit's initial HTML at image build.

No runtime monkey patches, network access, data reads or execution authority.
A one-shot DOM observer reveals the initial frame, then disconnects permanently.
The regular st.html stylesheet remains the non-container fallback.
The initial loading shell prevents unthemed placeholders flashing before the
first complete render; later reruns retain the already mounted application.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

START = "<!-- munshi-packaged-theme -->"
END = "<!-- /munshi-packaged-theme -->"
BOOT = """<style>
#munshi-boot-status{display:none}
html:not([data-munshi-ready]) body:not(:has([data-testid=stException])) [data-testid=stMain]{content-visibility:hidden;contain-intrinsic-size:auto 100vh}
html:not([data-munshi-ready]) body:not(:has([data-testid=stException])) #munshi-boot-status{
 display:flex;position:fixed;inset:0;align-items:center;justify-content:center;
 flex-direction:column;gap:1rem;background:#F6F7F4;color:#123D31;
 font-family:Manrope,system-ui,sans-serif;z-index:9999}
#munshi-boot-status strong{font-size:1.25rem;font-weight:750;letter-spacing:-.04em}
#munshi-boot-status span{font-size:.9rem;color:#44545B}
.stApp [data-testid=stElementContainer]:has(.munshi-boot-ready){display:none!important}
</style><script>
(()=>{
 let revealing=false;
 const hydrated=()=>document.querySelector('.munshi-boot-ready')&&!document.querySelector('[data-testid=stSkeleton]');
 const observer=new MutationObserver(()=>{
  if(document.querySelector('[data-testid=stException]')){observer.disconnect();return;}
  if(revealing||!hydrated())return;
  revealing=true;
  document.fonts.ready.then(()=>requestAnimationFrame(()=>requestAnimationFrame(()=>{
   if(hydrated()){document.documentElement.dataset.munshiReady='true';observer.disconnect();}
   else revealing=false;
  })));
 });
 observer.observe(document.documentElement,{childList:true,subtree:true});
})();
</script>"""
STATUS = '<div id="munshi-boot-status" role="status" aria-live="polite"><strong>MUNSHI</strong><span>Opening your workspace…</span></div>'


def package(source: str, theme: str) -> str:
    if START in source:
        before, rest = source.split(START, 1)
        source = before + rest.split(END, 1)[1]
    source = source.replace(STATUS, "")
    if "</head>" not in source or "<body>" not in source:
        raise ValueError("Unsupported Streamlit HTML: refusing an unverified patch")
    return source.replace("</head>", START + theme + BOOT + END + "</head>", 1).replace("<body>", "<body>" + STATUS, 1)


if __name__ == "__main__":
    import streamlit
    from app.product_studio import shell_css
    index = Path(streamlit.__file__).parent / "static/index.html"
    index.write_text(package(index.read_text(), shell_css()))
