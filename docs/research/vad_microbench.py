"""Synthetic VAD kernel microbenchmark; no microphone, network or transcripts."""
import argparse, hashlib, json, platform, time
from pathlib import Path
import numpy as np
import onnxruntime as ort
import webrtcvad
p=argparse.ArgumentParser();p.add_argument('--model',required=True);a=p.parse_args()
assert hashlib.sha256(Path(a.model).read_bytes()).hexdigest()=='597d30b3ec076608d059477bb14cfeffdf951bf5cae370d38f65d33bbfe82004'
rng=np.random.default_rng(6006)
fixtures={'silence':np.zeros(16000*10,dtype=np.int16),'noise':np.clip(rng.normal(0,327.68,16000*10),-32768,32767).astype(np.int16)}
rows=[]
for engine in ['webrtc','silero']:
 for name,samples in fixtures.items():
  start=time.perf_counter_ns()
  if engine=='webrtc':vad=webrtcvad.Vad(2);n=320
  else:
   opts=ort.SessionOptions();opts.intra_op_num_threads=1;opts.inter_op_num_threads=1
   session=ort.InferenceSession(a.model,opts,providers=['CPUExecutionProvider']);n=512
  init=(time.perf_counter_ns()-start)/1e6
  def run():
   if engine=='webrtc':vad=webrtcvad.Vad(2)
   state=np.zeros((2,1,128),np.float32);context=np.zeros((1,64),np.float32);dur=[];positive=0
   cpu=time.process_time_ns();wall=time.perf_counter_ns()
   for offset in range(0,len(samples)-n+1,n):
    chunk=samples[offset:offset+n];t=time.perf_counter_ns()
    if engine=='webrtc':decision=vad.is_speech(chunk.tobytes(),16000)
    else:
     x=chunk.astype(np.float32)[None,:]/32768
     y,state=session.run(None,{'input':np.concatenate([context,x],axis=1),'state':state,'sr':np.array(16000,np.int64)})
     context=x[:,-64:];decision=float(y[0,0])>=0.5
    dur.append((time.perf_counter_ns()-t)/1e6);positive+=int(decision)
   return {'frames':len(dur),'positive_frames':positive,'kernel_ms_p50':float(np.percentile(dur,50)),'kernel_ms_p95':float(np.percentile(dur,95)),'wall_ms':(time.perf_counter_ns()-wall)/1e6,'cpu_ms':(time.process_time_ns()-cpu)/1e6}
  first=run();warm=[run() for _ in range(5)]
  rows.append({'engine':engine,'fixture':name,'fixture_sha256':hashlib.sha256(samples.tobytes()).hexdigest(),'frame_ms':n/16,'init_ms':init,'first_pass':first,'warm_passes':warm})
print(json.dumps({'platform':platform.platform(),'python':platform.python_version(),'numpy':np.__version__,'onnxruntime':ort.__version__,'webrtcvad':webrtcvad.__version__,'seed':6006,'note':'Unpaced kernel cost, not capture latency, accuracy, idle CPU or end-to-end STT. First session is not OS-cache-cold. RAM unmeasured.','results':rows},indent=2))
