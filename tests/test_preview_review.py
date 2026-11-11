import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import engine
import studio_store as store
from planner import default_plan


class PreviewReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.old_data=store.DATA;self.old_engine=engine.DATA;store.DATA=self.root;engine.DATA=self.root
        with store.connection() as c:c.execute('CREATE TABLE records (kind TEXT,id TEXT,payload TEXT,PRIMARY KEY(kind,id))')

    def tearDown(self):
        store.DATA=self.old_data;engine.DATA=self.old_engine;self.tmp.cleanup()

    def test_background_does_not_count_as_overexposure(self):
        path=self.root/'image.png';image=Image.new('RGB',(100,100),'white')
        image.paste((90,20,10),(40,40,60,60));image.save(path)
        result={'product_samples':[{'x':.5,'y':.5,'material_name':'body'}],
                'studio':{'material_features':[{'name':'body','luminance':.3,'metallic':0,'transmission':0,'emission':0}]}}
        metrics=engine.product_metrics(path,result)
        self.assertEqual(metrics['opaque_clipped_fraction'],0)
        self.assertEqual(metrics['product_samples'],1)
        image=Image.new('RGB',(100,100),'white');image.save(path)
        self.assertEqual(engine.product_metrics(path,result)['opaque_clipped_fraction'],1)
        result['studio']['material_features'][0]['transmission']=1
        self.assertEqual(engine.product_metrics(path,result)['eligible_opaque_samples'],0)

    def test_effective_settings_are_same_for_preview_and_final(self):
        plan=default_plan();plan['exposure']=.5;plan['light_softness']=2
        plan['shot_adjustments']={'hero':{'exposure_delta':-.7,'softness_multiplier':.5}}
        effective=engine.effective_plan(plan,'hero')
        self.assertAlmostEqual(effective['exposure'],-.2);self.assertEqual(effective['light_softness'],1)
        self.assertEqual(engine.effective_plan(plan,'side')['exposure'],.5)
        self.assertEqual(plan['exposure'],.5)
        self.assertEqual(effective['shot_adjustments'],{})

    def fixtures(self,version=1):
        job={'id':'j','project_id':'p','version':1,'status':'running'}
        original={'job_id':'j','shot_id':'hero','quality':'preview','version':1,'png_url':'old.png'}
        project={'id':'p','version':version,'plan':default_plan(),'outputs':[copy.deepcopy(original)],'history':[]}
        store.put('job',job);store.put('project',project)
        return job,original

    def test_commit_preserves_old_snapshot_and_uses_new_revision(self):
        job,original=self.fixtures();adopted={**original,'png_url':'corrected.png'}
        engine.commit_review_results(job,[adopted],{'hero':{'exposure_delta':-.7}})
        project=store.get('project','p')
        self.assertEqual(project['version'],2);self.assertEqual(project['outputs'][0],original)
        self.assertEqual(project['outputs'][1]['version'],2)
        self.assertEqual(project['outputs'][1]['png_url'],'corrected.png')
        self.assertEqual(project['plan']['shot_adjustments']['hero']['exposure_delta'],-.7)

    def test_concurrent_edit_and_cancellation_are_never_overwritten(self):
        job,original=self.fixtures(version=2)
        engine.commit_review_results(job,[original],{'hero':{'exposure_delta':-.7}})
        self.assertEqual(len(store.get('project','p')['outputs']),1)
        self.assertNotIn('shot_adjustments',store.get('project','p')['plan'])
        job,original=self.fixtures();engine.cancel(job['id'])
        engine.commit_review_results(job,[original],{'hero':{'exposure_delta':-.7}})
        self.assertEqual(store.get('project','p')['version'],1)

    def test_pick_rejects_out_of_bounds_before_running_blender(self):
        job,original=self.fixtures()
        body={'output_id':'j','shot_id':'hero','quality':'preview','version':1,'points':[{'id':'a','x':float('nan'),'y':.5}]}
        with patch('engine.subprocess.run') as process:
            with self.assertRaises(ValueError):engine.pick_output('p',body)
            process.assert_not_called()

    def test_best_candidate_survives_cancel_and_retry(self):
        job,original=self.fixtures()
        folder=self.root/'p/runs/candidate';folder.mkdir(parents=True)
        for suffix in ['png','jpg']:
            Image.new('RGB',(8,8),'red').save(folder/('candidate.'+suffix))
        (folder/'candidate.blend').write_bytes(b'unit-test existence check')
        candidate={**original,'png_url':'/files/p/runs/candidate/candidate.png','jpg_url':'/files/p/runs/candidate/candidate.jpg',
                   'blend_url':'/files/p/runs/candidate/candidate.blend'}
        plan=default_plan();plan['shot_adjustments']={'hero':{'exposure_delta':-.7}}
        attempts=[{'round':0,'score':0},{'round':1,'score':.8}]
        engine.persist_review_candidate(job,original,candidate,plan,1,attempts)
        engine.cancel(job['id'])
        stored=store.get('project','p')['outputs'][0]
        recovered,adjustment=engine.recover_review_candidate(stored)
        self.assertEqual(recovered['png_url'],candidate['png_url'])
        self.assertEqual(adjustment,{'exposure_delta':-.7})
        self.assertEqual(stored['png_url'],'old.png')

    def run_correction_reviews(self,reviews):
        job,original=self.fixtures()
        job.update(plan=default_plan(),quality='preview')
        project=store.get('project','p');project['model_path']='fixture.glb'
        shot=job['plan']['shots'][0]
        original['effective_plan']=engine.effective_plan(job['plan'],shot['id'])
        remaining=iter(reviews)
        def inspect(job,record,result,plan,analysis):
            record['review']=copy.deepcopy(next(remaining))
            return record['review']
        def output(candidate_job,shot,out,result):
            return {**original,'png_url':out.name+'.png',
                    'effective_plan':engine.effective_plan(candidate_job['plan'],shot['id'])}
        with patch('engine.inspect_preview',side_effect=inspect), \
             patch('engine.execute',return_value={}) as execute, \
             patch('engine.output_record',side_effect=output), \
             patch('engine.persist_review_candidate'),patch('engine.atomic_json'):
            selected=engine.review_and_correct(job,project,shot,self.root/'preview',original,{})
        return selected,execute.call_count

    def review(self,score,patch):
        return {'source':'ai','provider':'fixture','model':'vision-fixture','score':score,'patch':patch,'needs_user':[],'status':'passed'}

    def test_unchanged_patch_does_not_report_unapplied_suggestions(self):
        (best,plan,selected_round),renders=self.run_correction_reviews([
            self.review(.8,{'exposure':0,'light_softness':1})])
        self.assertEqual(renders,0)
        self.assertEqual(selected_round,0)
        self.assertEqual(best['review']['needs_user'],[])
        self.assertEqual(best['review']['status'],'passed')

    def test_best_candidate_compares_patch_to_its_effective_parameters(self):
        (best,plan,selected_round),renders=self.run_correction_reviews([
            self.review(.6,{'exposure':-.7,'light_softness':1.4}),
            self.review(.9,{'exposure':-.7,'light_softness':1.4})])
        self.assertEqual(renders,1)
        self.assertEqual(selected_round,1)
        self.assertEqual(best['review']['needs_user'],[])
        self.assertEqual(best['review']['status'],'passed')
        self.assertEqual(best['effective_plan']['exposure'],-.7)

    def test_rejected_last_candidate_does_not_decide_best_image_notice(self):
        (best,plan,selected_round),renders=self.run_correction_reviews([
            self.review(.6,{'exposure':-.7}),
            self.review(.9,{'exposure':-1.4}),
            self.review(.7,{})])
        self.assertEqual(renders,2)
        self.assertEqual(selected_round,1)
        self.assertEqual(best['review']['score'],.9)
        self.assertEqual(best['review']['patch'],{'exposure':-1.4})
        self.assertEqual(best['review']['status'],'manual_review')
        self.assertTrue(any('仍有建议未采纳' in text for text in best['review']['needs_user']))


if __name__=='__main__':unittest.main()
