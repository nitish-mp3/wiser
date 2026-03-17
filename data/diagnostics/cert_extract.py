import ssl
import socket
import json
from datetime import datetime
host='192.168.1.169'
port=443
out={'host':host,'port':port,'timestamp':datetime.utcnow().isoformat()+'Z'}
ctx=ssl.create_default_context()
ctx.check_hostname=False
ctx.verify_mode=ssl.CERT_NONE
try:
    s=socket.create_connection((host,port),timeout=8)
    ss=ctx.wrap_socket(s,server_hostname='se-wifi-devices.local')
    cert=ss.getpeercert()
    out['cert']=cert
    ss.close()
except Exception as e:
    out['error']=str(e)
with open('/cert.json','w',encoding='utf-8') as f:
    json.dump(out,f,indent=2,default=str)
