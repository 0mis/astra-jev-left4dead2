"""Private capture test: game audio plus an excluded non-game probe sound."""
import datetime
import json
import pathlib
import time
import wave
import winsound
import numpy as np
from obs_guard import ROOT, audit, client


def main():
    test = ROOT / 'privacy-tests' / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    test.mkdir(parents=True, exist_ok=False)
    rate = 48000
    t = np.arange(rate * 2) / rate
    # Smooth, quiet three-band chirp. Only playback; no microphone is opened.
    phase = 2 * np.pi * (700 * t + (1600 / 4) * t**2)
    probe = (0.025 * np.sin(phase) * np.sin(np.pi * t / 2)**2).astype(np.float32)
    stereo = np.repeat(probe[:, None], 2, axis=1)
    with wave.open(str(test / 'external-probe.wav'), 'wb') as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes((stereo * 32767).astype('<i2').tobytes())
    c = client()
    before = audit(c)
    if before['recording']['outputActive']:
        raise RuntimeError('A recording already exists; not starting another')
    events = []
    result = None
    try:
        c.send('StartRecord')
        started = time.monotonic()
        events.append({'event': 'record_started', 't': 0})
        time.sleep(3)
        audit(c, require_recording=True)
        for index in range(3):
            events.append({'event': 'external_probe_begin', 'index': index, 't': time.monotonic() - started})
            winsound.PlaySound(str(test / 'external-probe.wav'), winsound.SND_FILENAME)
            events.append({'event': 'external_probe_end', 'index': index, 't': time.monotonic() - started})
            audit(c, require_recording=True)
            time.sleep(1)
        time.sleep(2)
        during = audit(c, require_recording=True)
    finally:
        if c.send('GetRecordStatus', raw=True)['outputActive']:
            result = c.send('StopRecord', raw=True)
    report = {'test': 'game process audio isolation', 'before': before, 'during': during,
              'events': events, 'recording': result, 'analysis_status': 'pending',
              'microphone_opened_by_test': False,
              'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    (test / 'capture-test.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'test_folder': str(test), 'recording': result, 'analysis_status': 'pending'}))


if __name__ == '__main__':
    main()
