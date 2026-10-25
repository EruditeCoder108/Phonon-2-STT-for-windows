from pathlib import Path
import json
import wave
import numpy as np

root=Path(__file__).parent
with wave.open(str(root/'voice-demo.wav')) as source:
    rate=source.getframerate()
    channels=source.getnchannels()
    samples=np.frombuffer(source.readframes(source.getnframes()),dtype='<i2').astype(np.float32)/32768
    if channels>1:samples=samples.reshape(-1,channels).mean(axis=1)
step=rate//100
rms=np.array([np.sqrt(np.mean(samples[i:i+step]**2)) for i in range(0,len(samples),step)])
scale=max(float(np.percentile(rms,94)),.01)
levels=np.clip((rms-.002)/(scale-.002),0,1)**.7
smoothed=[];v=0
for target in levels:
    v+=(float(target)-v)*(.42 if target>v else .10)
    smoothed.append(round(v,5))
words=json.loads((root/'voice-words.json').read_text(encoding='utf-8-sig'))
report={'rate':100,'source':'Local Microsoft Zira synthesis; RMS of the real audio signal',
        'seconds':len(samples)/rate,'values':smoothed,'words':words}
(root/'voice-envelope.json').write_text(json.dumps(report,separators=(',',':')),encoding='utf-8')
print({'seconds':report['seconds'],'markers':words,'peak_envelope':max(smoothed)})
