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
