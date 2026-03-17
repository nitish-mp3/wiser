import json
from datetime import datetime, UTC
import ssl
import websocket

targets=[
    'wss://192.168.1.169/',
    'wss://192.168.1.169/ws',
    'wss://192.168.1.169/socket'
]
rows=[]
for t in targets:
    row={'target':t,'timestamp':datetime.now(UTC).isoformat()}
    try:
        ws=websocket.create_connection(t,sslopt={'cert_reqs':ssl.CERT_NONE},timeout=6)
        row['connected']=True
        try:
            ws.settimeout(1)
            msg=ws.recv()
            row['first_frame']=str(msg)[:400]
        except Exception as inner:
            row['first_frame_error']=str(inner)
        ws.close()
    except Exception as e:
        row['connected']=False
        row['error']=str(e)
    rows.append(row)

with open('ws-probe.txt','w',encoding='utf-8') as f:
    for r in rows:
        f.write(json.dumps(r)+"\n")
