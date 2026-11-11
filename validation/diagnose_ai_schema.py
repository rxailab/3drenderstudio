"""Opt-in live diagnostic: save tool JSON only, never request bodies or headers."""
import argparse
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import ai_client
import ai_config
import engine
from studio_store import DATA, ROOT, get


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',required=True)
    parser.add_argument('--job',required=True)
    parser.add_argument('--shot',default='hero')
    args=parser.parse_args()
    project=get('project',args.project)
    output=next(o for o in project['outputs'] if o['job_id']==args.job and o['shot_id']==args.shot)
    config=ai_config.get_config()
    if not config.get('enabled') or not config.get('api_key'):
        raise ValueError('Configure and enable AI locally before this opt-in diagnostic.')
    report={'project_id':args.project,'job_id':args.job,'shot_id':args.shot,'version':output['version'],
            'provider':config['provider'],'model':config['model'],'started_at':time.time(),
            'network_calls':0,'responses':[]}
    original_post,original_response,original_check=ai_client._post,ai_client._response,ai_client._check_schema

    def post(*args,**kwargs):
        report['network_calls']+=1
        return original_post(*args,**kwargs)

    def response(*args,**kwargs):
        value=original_response(*args,**kwargs)
        report['responses'].append({'structured_response':copy.deepcopy(value)})
        return value

    def check(value,schema,path='$'):
        try:
            return original_check(value,schema,path)
        except ai_client.SchemaValidationError as error:
            if path=='$' and report['responses']:
                report['responses'][-1]['validation_error']=copy.deepcopy(error.feedback)
            raise

    ai_client._post,ai_client._response,ai_client._check_schema=post,response,check
    try:
        plan=output.get('effective_plan') or engine.effective_plan(project['plan'],args.shot)
        ai_client.review_preview(plan,project['analysis'],DATA/output['png_url'][7:],output.get('metrics',{}),config)
        report['success']=True
    except ValueError as error:
        report['success']=False
        report['error']=str(error)
    finally:
        ai_client._post,ai_client._response,ai_client._check_schema=original_post,original_response,original_check
        report['finished_at']=time.time()
        # The key never enters a prompt; nevertheless redact an exact echo.
        rendered=json.dumps(report,ensure_ascii=False,indent=2).replace(config['api_key'],'[redacted]')
        destination=ROOT/'validation'/'ai-schema-retry-debug.json'
        destination.write_text(rendered,encoding='utf-8')
        print(json.dumps({'success':report.get('success'), 'network_calls':report['network_calls'],
                          'validation_errors':[r.get('validation_error') for r in report['responses']],
                          'diagnostic_file':str(destination)},ensure_ascii=False))


if __name__=='__main__':
    main()
