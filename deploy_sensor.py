"""Hot-update only the original observation module, with a complete-file marker."""
import hashlib
import pathlib
from observe_game import EMS
root=pathlib.Path(__file__).resolve().parent
body=(root/'sensor'/'astra_sensor.nut').read_bytes()
metrics=(root/'sensor'/'astra_metrics.nut').read_bytes()
if len(body)>16000: raise RuntimeError('Observation source exceeds engine file-read limit')
if len(metrics)>16000: raise RuntimeError('Metrics source exceeds engine file-read limit')
version=hashlib.sha256(body+metrics).hexdigest()
for name,data in [('metrics-live.nut',metrics),('sensor-live.nut',body),('sensor-version.txt',version.encode())]:
    temp=EMS/(name+'.tmp'); temp.write_bytes(data); temp.replace(EMS/name)
print({'sensor_bytes':len(body),'metrics_bytes':len(metrics),'sha256':version})
