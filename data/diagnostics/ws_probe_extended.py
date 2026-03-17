import json, ssl, websocket
from datetime import datetime, UTC

target='wss://192.168.1.169/ws'
headers_sets=[
    [],
    ['User-Agent: WiserApp/3.2','Origin: https://192.168.1.169'],
    ['User-Agent: WiserApp/3.2','Origin: https://se-wifi-devices.local']
]
rows=[]
for hs in headers_sets:
    row={'target':target,'headers':hs,'timestamp':datetime.now(UTC).isoformat()}
    try:
        ws=websocket.create_connection(target,sslopt={'cert_reqs':ssl.CERT_NONE},timeout=6,header=hs)
        row['connected']=True
        events=[]
        for payload in ['ping','{}','{"type":"auth"}']:
            try:
                ws.send(payload)
                ws.settimeout(1)
                resp=ws.recv()
                events.append({'sent':payload,'recv':str(resp)[:300]})
            except Exception as e:
                events.append({'sent':payload,'error':str(e)})
        row['events']=events
        try:
            ws.close()
        except Exception:
            pass
    except Exception as e:
        row['connected']=False
        row['error']=str(e)
    rows.append(row)

with open('c:/Users/Lenovo/Documents/wirsy/prod/wiser/addon/data/diagnostics/ws-probe-extended.txt','w',encoding='utf-8') as f:
    for r in rows:
        f.write(json.dumps(r)+"\n")
