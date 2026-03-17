import json
from datetime import datetime, UTC
import paho.mqtt.client as mqtt
progress={'step':'final','status':'completed','ts':datetime.now(UTC).isoformat(),'report':'/data/report.txt'}
manual={'required':'wiser_hub_secret or cloud credentials','options':['wiser_hub_secret','cloud_api_base','cloud_access_token','cloud_subscription_key']}
c=mqtt.Client()
c.connect('192.168.1.203',1883,30)
c.publish('wiser/192_168_1_169/progress',json.dumps(progress),qos=1,retain=False)
c.publish('wiser/192_168_1_169/manual_action',json.dumps(manual),qos=1,retain=False)
c.disconnect()
