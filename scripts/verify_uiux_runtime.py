"""Browser acceptance of the real synthetic-only Streamlit runtime.

Refuses to click anything until the staging-only marker is present. Never point
this at production. Screenshots contain fictional fixtures, not production data.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import time

from playwright.sync_api import sync_playwright

VIEWS = ("dashboard", "jobs", "prepare-application", "auto-prepare", "tracker", "profile", "resume-studio", "research", "settings")
OBSERVE = """const supported=PerformanceObserver.supportedEntryTypes;
window.ux={cls:supported.includes('layout-shift')?0:null,shift_sum:supported.includes('layout-shift')?0:null,lcp:supported.includes('largest-contentful-paint')?0:null,events:[],supported};
let firstShift=0,lastShift=0,windowScore=0;
for(const type of ['layout-shift','largest-contentful-paint','event']){
 if(!PerformanceObserver.supportedEntryTypes.includes(type))continue;
 try{new PerformanceObserver(l=>l.getEntries().forEach(e=>{
  if(type==='layout-shift'&&!e.hadRecentInput){
   if(e.startTime-lastShift>1000||e.startTime-firstShift>5000){firstShift=e.startTime;windowScore=0;}
   lastShift=e.startTime;windowScore+=e.value;
   window.ux.cls=Math.max(window.ux.cls,windowScore);window.ux.shift_sum+=e.value;
  }
  if(type==='largest-contentful-paint')window.ux.lcp=e.startTime;
  if(type==='event'&&e.interactionId)window.ux.events.push(e.duration);
 })).observe({type,buffered:true,durationThreshold:16});}catch(e){}
}"""


def ready(page):
    page.wait_for_function("document.querySelector('#uiux-runtime') || document.querySelector('[data-testid=stException]')", timeout=45000)
    errors = page.locator('[data-testid="stException"]').all_text_contents()
    assert not errors, errors
    # During Streamlit rerender the previous and next status asides may
    # briefly coexist. The first is the active snapshot, not a second page.
    page.locator("#uiux-runtime").first.wait_for(timeout=45000)
    assert "Synthetic staging" in page.locator("#uiux-runtime").first.text_content()
    page.wait_for_function("!document.querySelector('[data-testid=stSkeleton]')", timeout=30000)
    if page.locator('#munshi-boot-status').count():
        page.locator('#munshi-boot-status').wait_for(state='hidden', timeout=30000)
    errors = page.locator('[data-testid="stException"]').all_text_contents()
    assert not errors, errors


def changed(page, previous):
    page.wait_for_function(
        "old=>Math.max(0,...Array.from(document.querySelectorAll('#uiux-runtime'))"
        ".map(node=>Number(node.dataset.runs)||0))>Number(old)",
        arg=previous, timeout=30000,
    )
    ready(page)


def mobile_dock(page):
    dock = page.locator('.st-key-product_mobile_routes')
    assert dock.bounding_box()['height'] <= 90, 'Bottom navigation must not cover the page'
    buttons = dock.get_by_role('button')
    assert buttons.count() == 5
    bounds = [button.bounding_box() for button in buttons.all()]
    assert max(b['y'] for b in bounds) - min(b['y'] for b in bounds) <= 1
    assert all(b['height'] >= 44 and b['width'] >= 44 for b in bounds)


def profile_acceptance(page, url, output):
    page.goto(url + '/?view=profile'); ready(page)
    intake = page.get_by_role('textbox', name='Review your source text', exact=True)
    if intake.count():
        source = ('Alex Fixture\nalex.fixture@example.invalid | New York, NY\n'
                  'PROFESSIONAL SUMMARY\nHR analyst with experience in people analytics and recruiting operations.\n'
                  'EXPERIENCE\nHR Analyst | Fictional Research Foundation | 2022 - 2025\n'
                  '- Built Excel and Power BI reporting for recruiting operations.\n'
                  'EDUCATION\nBachelor of Science in Human Resources | Fictional University | 2021\n'
                  'SKILLS\nExcel, Power BI, SQL, recruiting operations\n')
        previous = page.locator('#uiux-runtime').first.get_attribute('data-runs')
        page.locator('input[type=file]').set_input_files({'name':'synthetic-qa-resume.txt','mimeType':'text/plain','buffer':source.encode()})
        changed(page,previous)
        assert intake.input_value().strip() == source.strip()
        page.get_by_role('textbox', name='Source name', exact=True).fill('Synthetic QA Master Resume')
        intake.fill(source)
        page.get_by_text('I confirm this source is truthful and may be used to build my profile and job-specific resumes.', exact=True).click()
        assert page.get_by_role('checkbox', name='I confirm this source is truthful', exact=False).is_checked()
        page.get_by_role('button', name='Save Master Resume source', exact=True).click()
        page.get_by_role('button', name='Build profile from Master Resume', exact=True).wait_for()
    build = page.get_by_role('button', name='Build profile from Master Resume', exact=True)
    if build.count():
        build.click()
        page.get_by_role('button', name='Confirm as permanent profile', exact=True).wait_for()
    confirm = page.get_by_role('button', name='Confirm as permanent profile', exact=True)
    if confirm.count():
        previous = page.locator('#uiux-runtime').first.get_attribute('data-runs')
        confirm.click()
        changed(page, previous)
        page.get_by_text('Permanent MUNSHI profile confirmed. This reviewed snapshot is now the reusable profile authority.', exact=True).wait_for()
    page.get_by_text('confirmed Candidate Truth facts', exact=False).wait_for()
    page.get_by_role('button', name='Preview resume', exact=True).click()
    frame = page.frame_locator('iframe[title="st.iframe"]').first
    frame.locator('.paper').wait_for()
    assert 'Alex Fixture' in frame.locator('.paper').inner_text()
    assert frame.locator('.source-name').is_visible()
    assert frame.locator('.paper').bounding_box()['height'] > 200
    frame.locator('.paper').screenshot(path=str(output / 'chromium-profile-paper.png'))
    page.get_by_text('Master Resume preview', exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(output / 'chromium-profile-paper-visible.png'))
    previous = page.locator('#uiux-runtime').first.get_attribute('data-runs')
    page.get_by_role('button', name='Preview resume', exact=True).click()
    changed(page, previous)
    assert page.locator('iframe[title="st.iframe"]').count() == 0
    page.locator('.st-key-product_primary_routes').get_by_role('button', name='Resume Studio', exact=True).click()
    page.get_by_text('Resume appearance · preview only', exact=True).click()
    jake = page.get_by_role('button', name='Jake', exact=True)
    jake.click()
    # Streamlit rerenders the expander after the template click. Wait for the
    # selected template's settings to settle before measuring its touch target.
    page.get_by_role('combobox', name='Selected Computer Modern. Font', exact=True).wait_for(
        state='visible', timeout=15000,
    )
    jake.wait_for(state='visible', timeout=15000)
    box = jake.bounding_box()
    assert box is not None and box['height'] >= 44, {
        'box': box,
        'markup': jake.evaluate('element => element.outerHTML.slice(0, 700)'),
    }
    ready(page)
    page.screenshot(path=str(output / 'chromium-resume-appearance.png'))
    assert not page.locator('[data-testid="stException"]').count()


def workspace_acceptance(page, url, output):
    page.goto(url + '/?view=prepare-application'); ready(page)
    search = page.get_by_role('textbox', name='Search applications', exact=True)
    search.fill('no-such-synthetic-application');search.press('Enter')
    page.get_by_text('No applications match this view.',exact=True).wait_for()
    search.fill('');search.press('Enter')
    page.get_by_role('button',name='Open application',exact=True).first.wait_for()
    page.get_by_role('button',name='Open application',exact=True).first.click()
    page.get_by_role('button',name='Selected',exact=True).wait_for()
    assert 'application=' in page.url
    assert not page.locator('[data-testid=stException]').count()
    page.get_by_role('button',name='Close',exact=True).first.click()
    page.get_by_text('Select an application',exact=True).wait_for()
    page.screenshot(path=str(output / 'chromium-application-center-interacted.png'))
    page.goto(url + '/?view=tracker');ready(page)
    previous = page.locator('#uiux-runtime').first.get_attribute('data-runs')
    page.get_by_text('Inbox',exact=True).click()
    changed(page,previous)
    page.get_by_role('button',name='Connect Gmail',exact=True).wait_for()
    assert page.get_by_role('button',name='Connect Gmail',exact=True).is_disabled()
    assert 'tab=inbox' in page.url
    previous = page.locator('#uiux-runtime').first.get_attribute('data-runs')
    page.get_by_text('Pipeline',exact=True).click();changed(page,previous)
    page.get_by_role('combobox',name=re.compile(r'\. Status$')).wait_for()
    page.goto(url + '/?view=research');ready(page)
    for section in ('Companies','Role signals','Risks','Opportunities'):
        previous = page.locator('#uiux-runtime').first.get_attribute('data-runs')
        page.get_by_text(section,exact=True).click()
        changed(page,previous)
        assert not page.locator('[data-testid=stException]').count()
    page.goto(url + '/?view=settings');ready(page)
    page.get_by_role('button',name='Save review preference',exact=True).click()
    page.get_by_text('Review preference saved.',exact=True).wait_for()
    for section in ('Automation','Integrations','Profile & defaults','Credentials','Apply'):
        previous = page.locator('#uiux-runtime').first.get_attribute('data-runs')
        page.get_by_text(section,exact=True).click()
        changed(page,previous)
        assert not page.locator('[data-testid=stException]').count()
    page.goto(url + '/?view=jobs');ready(page)
    details = page.get_by_role('button',name='Open job details',exact=True).first
    details.focus()
    assert details.evaluate('e=>getComputedStyle(e).outlineStyle') != 'none'
    details.press('Enter');page.get_by_role('dialog').wait_for()
    page.keyboard.press('Escape');page.get_by_role('dialog').wait_for(state='hidden')
    assert not page.locator('[data-testid=stException]').count()


def run(url: str, output: Path, *, baseline: bool = False):
    output.mkdir(parents=True, exist_ok=True)
    results = {"url": url, "baseline": baseline, "routes": [], "interactions": []}
    with sync_playwright() as p:
        for engine in ("chromium", "webkit"):
            browser = getattr(p, engine).launch(headless=True)
            for width in (1440, 390):
                page = browser.new_page(viewport={"width": width, "height": 1000 if width > 900 else 844}, has_touch=width < 900)
                page.route("https://**/*", lambda route: route.abort())
                page.add_init_script(OBSERVE)
                console_errors = []
                page.on("pageerror", lambda error: console_errors.append(str(error)))
                for view in VIEWS:
                    started = time.perf_counter()
                    page.goto(url + "/?view=" + view, wait_until="domcontentloaded")
                    ready(page)
                    if not baseline:
                        assert page.evaluate("[...document.fonts].some(f=>f.family==='Manrope'&&f.status==='loaded')")
                        assert page.locator('.brand-mark img').evaluate('e=>e.complete&&e.naturalWidth>0')
                    if view in {"dashboard", "jobs"}:
                        page.get_by_role("button", name="Save", exact=True).first.wait_for(timeout=30000)
                    if view == "dashboard":
                        page.locator('[data-testid="stMetricValue"]').first.wait_for(timeout=30000)
                    overflow = page.locator('[data-testid="stMain"]').evaluate("e=>e.scrollWidth-e.clientWidth")
                    if not baseline:
                        assert overflow <= 1, (engine, width, view, "horizontal overflow", overflow)
                    elapsed = time.perf_counter() - started
                    snapshot = f"{engine}-{width}-{view}.png"
                    page.screenshot(path=str(output / snapshot))
                    results["routes"].append({"engine": engine, "width": width, "view": view,
                        "ready_seconds": round(elapsed, 3),
                        "server_seconds": float(page.locator("#uiux-runtime").first.get_attribute("data-server-seconds")),
                        "vitals": page.evaluate("window.ux"), "overflow_px": overflow, "screenshot": snapshot})
                    print(json.dumps(results["routes"][-1]), flush=True)
                    (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
                if width < 900 and not baseline:
                    page.goto(url + "/?view=jobs");ready(page)
                    mobile_dock(page)
                    page.get_by_role("button", name="Open job details", exact=True).first.wait_for()
                    page.locator('.job-title').first.tap()
                    assert page.get_by_role("dialog").count() == 0
                    page.get_by_role("button", name="Open job details", exact=True).first.tap()
                    page.get_by_role("dialog").wait_for()
                    page.get_by_role("button", name="Close", exact=True).first.tap()
                    page.get_by_role("dialog").wait_for(state="hidden")
                    page.locator('.st-key-product_mobile_routes').get_by_role("button", name="Home", exact=True).tap()
                    page.locator('.studio-command').wait_for()
                    menu = page.locator('.mobile-nav summary')
                    menu.tap()
                    assert page.locator('.mobile-nav').evaluate('e=>e.open')
                    menu.tap()
                    assert not page.locator('.mobile-nav').evaluate('e=>e.open')
                    results["interactions"].append({"engine":engine,"width":width,"action":"mobile drawer, ordinary touch, dock and menu", "passed":True})
                assert not console_errors, console_errors
                page.close()
            if not baseline:
                page = browser.new_page(viewport={"width":1440,"height":1000}, has_touch=True, reduced_motion="reduce", color_scheme="dark")
                for view in ('dashboard', 'jobs', 'profile'):
                    page.goto(url + '/?view=' + view);ready(page)
                    for width in (375,430,768,1280,1920):
                        page.set_viewport_size({'width':width,'height':844 if width<900 else 1000})
                        assert page.locator('[data-testid="stMain"]').evaluate('e=>e.scrollWidth-e.clientWidth') <= 1
                        if width < 900:
                            mobile_dock(page)
                        page.screenshot(path=str(output / f'{engine}-{width}-{view}.png'))
                        results['interactions'].append({'engine':engine,'width':width,'action':view+' responsive layout','passed':True})
                page.set_viewport_size({'width':844,'height':390})
                assert page.locator('[data-testid="stMain"]').evaluate('e=>e.scrollWidth-e.clientWidth') <= 1
                mobile_dock(page)
                page.screenshot(path=str(output / f'{engine}-landscape-profile.png'))
                page.set_viewport_size({'width':375,'height':844})
                page.evaluate("document.documentElement.style.setProperty('font-size','32px','important')")
                assert page.evaluate('getComputedStyle(document.documentElement).fontSize') == '32px'
                assert page.locator('[data-testid="stMain"]').evaluate('e=>e.scrollWidth-e.clientWidth') <= 1
                dock_height = page.locator('.st-key-product_mobile_routes').bounding_box()['height']
                assert dock_height <= 160
                assert page.locator('.block-container').evaluate('e=>parseFloat(getComputedStyle(e).paddingBottom)') > dock_height
                assert 'light' in page.evaluate('getComputedStyle(document.documentElement).colorScheme')
                page.screenshot(path=str(output / f'{engine}-large-text-profile.png'))
                results['interactions'].append({'engine':engine,'action':'landscape, 32px root text, reduced motion and dark host preference','passed':True})
                page.close()
            if engine == "chromium" and not baseline:
                page = browser.new_page(viewport={"width":1440,"height":1000})
                page.add_init_script(OBSERVE)
                page.route("https://**/*", lambda route: route.abort())
                page.goto(url + "/?view=dashboard");ready(page)
                page.evaluate("window.uiuxDocumentSentinel='retained'")
                previous = page.locator("#uiux-runtime").first.get_attribute("data-runs")
                started = time.perf_counter()
                page.locator('.st-key-product_primary_routes').get_by_role("button", name="Browse jobs", exact=True).click()
                changed(page, previous)
                assert page.evaluate("window.uiuxDocumentSentinel") == "retained"
                assert "view=jobs" in page.url
                results["interactions"].append({"action":"native navigation", "seconds":round(time.perf_counter()-started,3),"document_preserved":True})

                button = page.get_by_role("button", name="Save", exact=True).nth(12)
                button.scroll_into_view_if_needed()
                scroll_before = page.locator('[data-testid="stMain"]').evaluate('e=>e.scrollTop')
                saved_before = page.get_by_role('button',name='Saved',exact=True).count()
                previous = page.locator("#uiux-runtime").first.get_attribute("data-runs")
                started = time.perf_counter()
                button.click()
                page.wait_for_function("count=>[...document.querySelectorAll('[data-testid=stButton] button')].filter(e=>e.textContent.trim()==='Saved').length>count",arg=saved_before)
                scroll_after = page.locator('[data-testid="stMain"]').evaluate('e=>e.scrollTop')
                assert abs(scroll_after-scroll_before) <= 48, (scroll_before,scroll_after)
                assert page.locator("#uiux-runtime").first.get_attribute("data-runs") == previous
                results["interactions"].append({"action":"save job", "passed":True,"full_page_reruns":0,"seconds":round(time.perf_counter()-started,3),"scroll_delta_px":round(scroll_after-scroll_before,2)})

                page.get_by_role("button", name="Open job details", exact=True).first.click()
                dialog = page.get_by_role("dialog")
                dialog.wait_for()
                dialog.get_by_role("button", name="Close", exact=True).first.click()
                dialog.wait_for(state="hidden")
                page.locator('.job-title').first.click()
                assert page.get_by_role("dialog").count() == 0
                results["interactions"].append({"action":"details open/dismiss and ordinary card touch", "passed":True})

                # A native filter rerun must retain the same browser document.
                search = page.get_by_role("textbox", name="Search jobs, companies, or locations", exact=True)
                previous = page.locator("#uiux-runtime").first.get_attribute("data-runs")
                search.fill("Compensation");search.press("Enter")
                page.wait_for_function("[...document.querySelectorAll('.job-title')].length>0 && [...document.querySelectorAll('.job-title')].every(e=>e.textContent.includes('Compensation'))")
                assert page.locator("#uiux-runtime").first.get_attribute("data-runs") == previous
                assert page.locator('.job-title').count() > 0
                assert all("Compensation" in text for text in page.locator('.job-title').all_text_contents())
                assert page.evaluate("window.uiuxDocumentSentinel") == "retained"
                results["interactions"].append({"action":"live search", "passed":True})

                filters = page.locator('.st-key-jobs_filter_disclosure')
                filters.locator('summary').click()
                page.get_by_role('combobox',name=re.compile(r'\. Location$')).click()
                page.get_by_role('option',name='Remote, US',exact=True).click()
                page.wait_for_function("[...document.querySelectorAll('.job-top')].length>0&&[...document.querySelectorAll('.job-top')].every(e=>e.textContent.includes('Remote, US'))")
                assert filters.locator('details').evaluate('e=>e.open')
                page.get_by_role('button',name='Reset filters',exact=True).click()
                page.wait_for_function("document.querySelector('input[aria-label=\"Search jobs, companies, or locations\"]')?.value===''")
                assert page.locator('#uiux-runtime').first.get_attribute('data-runs') == previous
                results['interactions'].append({'action':'filter disclosure, location selection, persistent expansion and reset','passed':True,'full_page_reruns':0})

                previous = page.locator("#uiux-runtime").first.get_attribute("data-runs")
                page.locator('.st-key-product_primary_routes').get_by_role("button", name="Dashboard", exact=True).click()
                changed(page, previous)
                panel = page.get_by_text("Application engines · MUNSHI Native & Relay", exact=True)
                panel.scroll_into_view_if_needed()
                before = page.locator("#uiux-runtime").first.get_attribute("data-runs")
                panel.click()
                page.get_by_text("Application engines", exact=True).wait_for()
                assert page.locator("#uiux-runtime").first.get_attribute("data-runs") == before
                results["interactions"].append({"action":"engine status fragment", "full_page_reruns":0})
                results['interactions'].append({'action':'browser Event Timing samples (not field INP)','durations_ms':page.evaluate('window.ux.events')})
                page.close()
            browser.close()
        if not baseline:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width':1440,'height':1000})
            page.route('https://**/*', lambda route:route.abort())
            workspace_acceptance(page,url,output)
            results['interactions'].append({'action':'application selection/search/close, tracker inbox/pipeline, research tabs, settings save/sections, keyboard drawer','passed':True})
            profile_acceptance(page,url,output)
            results['interactions'].append({'action':'source intake, local profile build, explicit confirmation, visible paper preview, appearance controls','passed':True})
            browser.close()
    (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    run(args.url.rstrip("/"), args.output, baseline=args.baseline)
