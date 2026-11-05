from copy import deepcopy
import json
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from agenteval.db import Agent, BaselineSetting, BenchmarkVersion, EvaluationDraft, Run, ReleaseDecision
from test_team_security import team, resources
from test_product_workflow import guided_body


def context(client):
    def post(path, body=None):
        if path=='profile-versions': body={**body,'name':body['name']+'-'+str(uuid4())[:8],'evaluators':body['evaluators']+[{**body['evaluators'][0],'instance_id':'hard_gate','hard_constraint':True}]}
        response = client.post('/api/v1/'+path, json=body)
        assert response.status_code in (200,201,202), response.text
        return response.json()
    project = post('projects', {'name':'Product completion'})
    return {'post':post,'admin':client,'a':project}


def test_reuse_preflight_and_unknown_result_use_one_committed_run_without_new_resources(client):
    t=context(client); a=resources(t,t['a']); p=t['post']; pid=t['a']['id']
    copied=client.get(f"/api/v1/evaluations/{a['run']['id']}/reuse-config").json()
    body={**a['body'],'project_id':pid,'request_key':str(uuid4())}
    before=client.get(f'/api/v1/workspace/{pid}').json()['totals']
    preview=p('onboarding/reuse-preview',body)
    assert preview['case_count']==1 and preview['judge']['initial_calls']==0
    assert client.get(f'/api/v1/workspace/{pid}').json()['totals']==before
    assert copied['agent_version_id']==body['agent_version_id']
    first=p('onboarding/reuse',body); second=p('onboarding/reuse',body)
    assert first['id']==second['id']!=a['run']['id']
    queried=client.get(f"/api/v1/onboarding/requests/{body['request_key']}?project_id={pid}").json()
    assert queried['found'] and queried['run']['id']==first['id']
    alternate=p('profile-versions',{'project_id':pid,'name':'Other','version':'1','evaluators':[{'instance_id':'answer','id':'exact_match'}]})
    assert client.post('/api/v1/onboarding/reuse',json={**body,'profile_version_id':alternate['id']}).status_code==409
    assert client.get(f'/api/v1/workspace/{pid}').json()['totals']['agents']==before['agents']


def test_guided_preview_is_side_effect_free_and_matches_execution_validation(client):
    t=context(client); body=guided_body(t['a']['id']); p=t['post']
    summary=p('onboarding/preview',body)
    assert summary['planned_trials']==1
    assert client.get(f"/api/v1/agents?project_id={t['a']['id']}").json()['total']==0
    bad=deepcopy(body); bad['agent']['capabilities']=[]; bad['goals']=['tools']
    preview=client.post('/api/v1/onboarding/preview',json=bad)
    actual=client.post('/api/v1/onboarding/evaluations',json=bad)
    assert preview.status_code==actual.status_code==422
    assert preview.json()['detail']['code']==actual.json()['detail']['code']=='PREFLIGHT_FAILED'
    assert client.get(f"/api/v1/agents?project_id={t['a']['id']}").json()['total']==0


def test_document_preflight_is_atomic_precise_and_version_diff_keeps_originals(client):
    t=context(client); p=t['post']; bm=p('benchmarks',{'project_id':t['a']['id'],'name':'Versioned cases'})
    case={'case_key':'one','input':{'nested':{'items':[1,{'key':'value'}]}},'assertions':{'expected_output':'ok'},'tags':['golden']}
    duplicate=json.dumps(case)+'\n\n'+json.dumps(case)
    preview=p(f"benchmarks/{bm['id']}/preview",{'document':duplicate})
    assert not preview['valid'] and any(e['line']==3 and e['field']=='/case_key' for e in preview['errors'])
    pretty=json.dumps({'cases':[case,case]},indent=2)
    wrapped=p(f"benchmarks/{bm['id']}/preview",{'document':pretty})
    duplicate_error=next(e for e in wrapped['errors'] if e['code']=='DUPLICATE_CASE_KEY')
    assert duplicate_error['line']>3 and duplicate_error['row']==2

    assert client.post(f"/api/v1/benchmarks/{bm['id']}/publish",json={'version':'bad','document':duplicate}).status_code==422
    assert client.get(f"/api/v1/benchmarks/{bm['id']}/versions").json()['total']==0
    malformed=p(f"benchmarks/{bm['id']}/preview",{'document':json.dumps(case)+'\ninvalid'})
    assert malformed['errors'][0]['line']==2
    absent=p(f"benchmarks/{bm['id']}/preview",{'document':json.dumps({**case,'assertions':{}})})
    assert absent['errors'][0]['field']=='/assertions'
    nonfinite=p(f"benchmarks/{bm['id']}/preview",{'document':'[{"case_key":"bad","input":{"value":NaN},"assertions":{"expected_output":"ok"}}]'})
    assert not nonfinite['valid'] and nonfinite['errors'][0]['code']=='NON_FINITE_OR_TOO_DEEP_JSON'

    v1=p(f"benchmarks/{bm['id']}/publish",{'version':'1','document':json.dumps([case])})
    changed=deepcopy(case); changed['input']['nested']['items'][1]['key']='updated'
    new={**deepcopy(case),'case_key':'two'}
    v2=p(f"benchmarks/{bm['id']}/publish",{'version':'2','document':json.dumps([changed,new])})
    diff=client.get(f"/api/v1/benchmark-versions/{v1['id']}/diff?other_version_id={v2['id']}").json()
    assert diff['added']==['two'] and diff['modified'][0]['case_key']=='one'
    original=client.get(f"/api/v1/benchmark-versions/{v1['id']}/cases").json()['items'][0]['definition']
    assert original==case and diff['modified'][0]['before']==case


def test_explicit_baseline_stays_fixed_after_later_old_version_runs_and_checks_scope(client):
    t=context(client); a=resources(t,t['a']); b=resources(t,t['a']); p=t['post']; pid=t['a']['id']
    body={'agent_id':a['agent']['id'],'benchmark_version_id':a['bv']['id'],'grading_batch_id':a['run']['grading_batch_id']}
    created=p('quality/baselines/'+pid,body)
    assert client.post('/api/v1/quality/baselines/'+pid,json=body).status_code==409
    assert client.post('/api/v1/quality/baselines/'+pid,json={**body,'expected_revision':1,'grading_batch_id':b['run']['grading_batch_id']}).status_code==422
    p('evaluations',a['body'])
    persisted=client.get('/api/v1/quality/baselines/'+pid).json()['items'][0]
    assert persisted['grading_batch_id']==body['grading_batch_id'] and persisted['revision']==created['revision']
    params={'agent_id':a['agent']['id'],'candidate_version_id':a['av']['id'],'benchmark_version_id':a['bv']['id'],'candidate_batch_id':body['grading_batch_id']}
    workbench=client.get('/api/v1/quality/workbench/'+pid,params=params).json()
    assert workbench['candidate']['grading_batch_id']==body['grading_batch_id']
    assert workbench['evaluation_scope']['baseline_batch_id']==body['grading_batch_id']
    invalid=client.get('/api/v1/quality/workbench/'+pid,params={**params,'candidate_batch_id':b['run']['grading_batch_id']})
    assert invalid.status_code==422


def test_current_release_validity_is_separate_from_frozen_snapshot(client):
    t=context(client); a=resources(t,t['a']); p=t['post']; did=a['decision']['id']
    original=client.get('/api/v1/release-decisions/'+did).json()
    assert original['current_validity']['current']
    other=resources(t,t['a']); issue=other['issue']
    p(f"quality/issues/{issue['id']}/update",{'expected_revision':issue['revision'],'status':'confirmed','note':'Other agent should not invalidate'})
    assert client.get('/api/v1/release-decisions/'+did).json()['current_validity']['current']
    issue=a['issue']; p(f"quality/issues/{issue['id']}/update",{'expected_revision':issue['revision'],'status':'confirmed','note':'New applicable blocker'})
    updated=client.get('/api/v1/release-decisions/'+did).json()
    assert updated['snapshot']==original['snapshot'] and updated['status']==original['status']
    assert 'APPLICABLE_BLOCKING_ISSUES_CHANGED' in updated['current_validity']['reasons']
    p(f"evaluations/{a['run']['id']}/regrade")
    assert 'NEWER_GRADING_BATCH_EXISTS' in client.get('/api/v1/release-decisions/'+did).json()['current_validity']['reasons']
    assert '当前有效性' in client.get(f'/api/v1/release-decisions/{did}/export?format=html').text


def test_private_drafts_are_member_project_isolated_and_not_raw_evidence_bypass(team):
    t=team; c=t['developer']['client']; pid=t['a']['id']; key=str(uuid4())
    payload={'request_key':key,'cases':'[{"input":{"email":"private@example.com","password":"author-only-value"}}]'}
    made=c.post('/api/v1/onboarding/drafts/'+pid,json={'payload':payload})
    assert made.status_code==200, made.text
    restored=c.get('/api/v1/onboarding/drafts/'+pid)
    assert restored.headers['x-agenteval-view']=='private-owner-draft'
    assert restored.json()['payload']['cases']==payload['cases']
    assert t['reader']['client'].get('/api/v1/onboarding/drafts/'+pid).json()['payload'] is None
    assert t['outsider']['client'].get('/api/v1/onboarding/drafts/'+pid).status_code==404
    assert c.post('/api/v1/onboarding/drafts/'+pid,json={'payload':payload}).status_code==409
    assert t['reader']['client'].post('/api/v1/onboarding/drafts/'+pid,json={'payload':payload}).status_code==403
    assert c.post('/api/v1/onboarding/drafts/'+pid,json={'expected_revision':1,'payload':{**payload,'endpoint':'https://user:secret@example.com'}}).status_code==422
    assert c.get('/api/v1/evaluations?project_id='+pid).headers['x-agenteval-view']=='redacted'


def test_new_workflow_routes_enforce_cross_project_references_and_viewer_writes(team):
    t=team; a=resources(t,t['a']); b=resources(t,t['b']); c=t['developer']['client']; reader=t['reader']['client']
    body={**a['body'],'project_id':t['a']['id'],'request_key':str(uuid4())}
    assert reader.post('/api/v1/onboarding/reuse-preview',json=body).status_code==403
    assert c.post('/api/v1/onboarding/reuse-preview',json={**body,'profile_version_id':b['profile']['id']}).status_code==404
    assert c.get(f"/api/v1/benchmark-versions/{a['bv']['id']}/diff?other_version_id={b['bv']['id']}").status_code==404
    assert c.get(f"/api/v1/evaluations/{b['run']['id']}/reuse-config").status_code==404
    assert c.post('/api/v1/onboarding/drafts/'+t['a']['id'],json={'payload':{'request_key':str(uuid4()),'agent_version_id':b['av']['id']}}).status_code==404
    assert c.get('/api/v1/quality/baselines/'+t['a']['id'],params={'agent_id':b['agent']['id']}).status_code==404
