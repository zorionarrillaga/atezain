"""The current evaluation and application share a bounded provider request contract."""
import json
from api.configuration import served_llm
from agent.llm import GroqLLM
from redteam.served import ActualContextCache


def test_served_request_uses_json_and_preserves_legacy_defaults(monkeypatch):
    import urllib.request
    requests=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):return b'{"choices":[{"message":{"content":"{}"}}]}'
    def send(request,timeout):
        requests.append(json.loads(request.data));return Response()
    monkeypatch.setattr(urllib.request,'urlopen',send)
    served_llm('openai/gpt-oss-120b','test-secret').complete('JSON','{}')
    assert requests[0]['response_format']=={'type':'json_object'}
    assert requests[0]['reasoning_effort']=='low' and requests[0]['max_completion_tokens']==2048
    assert 'test-secret' not in json.dumps(requests)
    GroqLLM(api_key='test-secret').complete('JSON','{}')
    assert 'response_format' not in requests[1] and 'reasoning_effort' not in requests[1]


def test_current_cache_binds_actual_input_and_generation_options(tmp_path):
    class Fixture:
        json_mode=True
        temperature=0
        max_output_tokens=2048
        reasoning_effort='low'
        def complete(self,system,user):return json.dumps({'summary':user})
    llm=Fixture();a=ActualContextCache(llm,'fixture',tmp_path,'policy-a',None)
    assert a.complete('system','customer-a')==a.complete('system','customer-a') and a.hit
    before=a.key
    a.complete('system','customer-b');assert a.key!=before and not a.hit
    a.complete('system','customer-a');assert a.key==before and a.hit
    llm.json_mode=False
    a.complete('system','customer-a');assert a.key!=before and not a.hit


def test_current_evaluation_proves_malformed_output_is_refused_before_writes():
    from redteam.run import run_case, load_cases, CASES
    from api.configuration import served_policy
    class Malformed:
        def complete(self,system,user):
            return '{"summary":"Example","proposals":[{"action":"update_amount","params":{"amount":0}},"unexpected"]}'
    row=run_case(load_cases(CASES)[0],Malformed(),'fixture',config=served_policy('enterprise'),retrieval='customer',use_cache=False,capture_refusals=True)
    assert row['model_output_error']=='invalid_proposals' and row['refused_before_policy']
    assert row['records_unchanged'] and row['audit_ok'] and not row['executed_on']
    assert row['executed_off'] is None and row['n_proposals']==0


def test_the_date_wrapper_sits_outside_the_cache_and_changes_the_cached_input(tmp_path):
    """`redteam/served.py --wrapper solo-date`: the wrapper is OUTSIDE the cache, so the cached input
    is what the model was actually shown — the date and the unsent-draft paragraph included — and
    the plain run's cache key is not the wrapped run's. The plain fingerprint is untouched: only a
    wrapped run names the wrapper and `ops/solo_demo.py` among its sources."""
    import datetime
    from ops.solo_demo import SoloModel
    class Fixture:
        json_mode=True;temperature=0;max_output_tokens=2048;reasoning_effort='low'
        def complete(self,system,user):
            self.system,self.user=system,user;return json.dumps({'summary':'ok'})
    llm=Fixture();user=json.dumps({'task':'draft','context':{'invoice':{'id':'F-1'}}},ensure_ascii=False,indent=1)
    plain=ActualContextCache(llm,'fixture',tmp_path,'fp',None);plain.complete('system',user);plain_key=plain.key
    inner=ActualContextCache(llm,'fixture',tmp_path,'fp',None)
    SoloModel(inner,today=datetime.date(2026,9,5)).complete('system',user)
    assert inner.key!=plain_key and not inner.hit, 'the wrapped input is another input'
    shown=json.loads((tmp_path/'fixture'/(inner.key+'.json')).read_text())
    assert shown['key']==inner.key and json.loads(llm.user)['evaluation_date']=='2026-09-05' and 'NO envía mensajes' in llm.system
    SoloModel(inner,today=datetime.date(2026,9,5)).complete('system',user)
    assert inner.hit, 'the same declared date is the same cached input'
