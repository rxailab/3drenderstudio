"""Drag a real material while a production preview is queued and camera settings are unsaved."""
from playwright.sync_api import sync_playwright
from pathtrace_optics_smoke import project_server, open_project, sphere_geometry


def run():
    with project_server() as (url, projects), sync_playwright() as pw:
        import engine
        from studio_store import get
        ident = projects['base']
        with pw.chromium.launch(channel='chrome', headless=True,
                                args=['--use-angle=swiftshader','--enable-unsafe-swiftshader']) as browser:
            page = browser.new_page(viewport={'width':1320,'height':800},device_scale_factor=1,has_touch=True)
            page.set_default_timeout(60000)
            errors = []
            page.on('pageerror',lambda error: errors.append(str(error)))
            page.goto(url)
            open_project(page,ident)
            before = get('project',ident)
            slider = page.locator('#exposure')
            slider.focus()
            slider.press('ArrowRight')
            page.wait_for_function('dirty')
            draft_exposure = slider.input_value()
            assert float(draft_exposure) != before['plan']['exposure']
            job = engine.submit(ident,'preview',shot_ids=['hero'])
            page.wait_for_function("activeJob()?.kind==='preview'")
            page.locator('#localMaterialCategory').select_option('Metal')
            card = page.locator('#localMaterialGrid .local-material-tile').first
            material = card.get_attribute('data-id')
            start = card.bounding_box()
            sphere = sphere_geometry(page,'Silver sphere')
            frame = page.locator('#realtimeViewport .realtime-canvas-frame').bounding_box()
            end = frame['x']+sphere['x'], frame['y']+sphere['y']

            def drag_to(point):
                page.mouse.move(start['x']+start['width']/2,start['y']+start['height']/2)
                page.mouse.down()
                page.mouse.move(*point,steps=12)

            # An empty part of the white background must never become a saved edit.
            drag_to((frame['x']+20,frame['y']+30))
            page.mouse.up()
            assert get('project',ident)['version'] == before['version']
            drag_to(end)
            page.wait_for_function("realtime.instance.previewTarget?.object_name==='Silver sphere'")
            assert page.locator('.material-drag-preview').is_visible()
            with page.expect_response(lambda res:res.url.endswith('/materials/drop') and res.request.method=='POST') as response:
                page.mouse.up()
            assert response.value.status == 200,response.value.text()
            page.wait_for_function('current.version===2 && !busy && realtime.status==="ready"')
            saved = get('project',ident)
            slot = saved['plan']['object_material_overrides'][0]
            assert slot['catalog_id']==material and slot['object_name']=='Silver sphere', slot
            assert saved['plan']['exposure']==before['plan']['exposure']
            assert slider.input_value()==draft_exposure
            assert page.evaluate('dirty && draftBaseVersion===current.version')
            assert page.evaluate('draft.object_material_overrides[0].catalog_id')==material
            assert get('job',job['id'])['version']==before['version']
            assert not page.locator('.material-drag-preview').count()
            # A pen or finger uses the same picker without requiring HTML drag/drop.
            glass = sphere_geometry(page,'Glass sphere')
            start = card.bounding_box()
            origin = start['x']+start['width']/2,start['y']+start['height']/2
            destination = frame['x']+glass['x'], frame['y']+glass['y']
            touch = page.context.new_cdp_session(page)
            touch.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':origin[0],'y':origin[1]}]})
            for step in range(1,9):
                x,y = [a+(b-a)*step/8 for a,b in zip(origin,destination)]
                touch.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x,'y':y}]})
            page.wait_for_function("realtime.instance.previewTarget?.object_name==='Glass sphere'")
            with page.expect_response(lambda res:res.url.endswith('/materials/drop') and res.request.method=='POST') as response:
                touch.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
            assert response.value.status==200,response.value.text()
            assert {s['object_name'] for s in get('project',ident)['plan']['object_material_overrides']}=={'Silver sphere','Glass sphere'}
            assert not errors,errors
            print('Empty drop ignored; mouse and touch previewed and saved to separate parts with an older render queued and exposure draft intact.')


if __name__=='__main__':
    run()
