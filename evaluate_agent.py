"""Small disclosed Jev behavior evaluation; no gameplay input or screenshots."""
import datetime
import json
from agent_state import EvidenceMemory
from agent_policy import packet
from test_general_agent import scenarios,FlatNav
from jev_bridge import JevClient,ROOT

def main():
    client=JevClient();before=client.spent;rows=[]
    try:
        for name,state,expected in scenarios():
            data,questions,_=packet(state,FlatNav(),EvidenceMemory())
            result=client.request(data,questions)
            failures={k:{'expected_any':sorted(v),'actual':result['answers'][k]['choice']} for k,v in expected.items() if result['answers'][k]['choice'] not in v}
            rows.append({'case':name,'passed':not failures,'failures':failures,'result':result})
            print(json.dumps({'case':name,'passed':not failures,'failures':failures}),flush=True)
    finally:
        report={'at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cases':rows,'passed':sum(r['passed'] for r in rows),
            'total':len(rows),'additional_usd':client.spent-before,'conservative_total_usd':client.spent,
            'scope':'Designed regression scenarios, not a campaign benchmark. No claim of universal accuracy.'}
        (ROOT/'agent-evaluation.json').write_text(json.dumps(report,indent=2),encoding='utf-8');client.close()
        print(json.dumps({k:v for k,v in report.items() if k!='cases'}))

if __name__=='__main__':main()
