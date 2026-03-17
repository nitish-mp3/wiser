import json
from datetime import datetime, UTC
import paho.mqtt.client as mqtt
payload={'step':'1-3','status':'completed','ts':datetime.now(UTC).isoformat()}
manual={'message':'Diagnostics steps 1-3 done; proceeding with cloud/local API documentation correlation.'}
c=mqtt.Client()
c.connect('192.168.1.203',1883,30)
c.publish('wiser/192_168_1_169/progress',json.dumps(payload),qos=1,retain=False)
c.publish('wiser/192_168_1_169/manual_action',json.dumps(manual),qos=1,retain=False)
c.disconnect()
