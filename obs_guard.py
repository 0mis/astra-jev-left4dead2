"""OBS capture allowlist. No microphone/system-audio fallback is permitted."""
import json
import pathlib
import shutil
import time
import obsws_python

ROOT = pathlib.Path(__file__).resolve().parent
TARGET = 'Left 4 Dead 2 - Direct3D 9:Valve001:left4dead2.exe'
NAME = 'L4D2 only'


def client():
    return obsws_python.ReqClient(**json.loads(
        (ROOT / 'obs-control.private.json').read_text(encoding='utf-8')), timeout=8)


def finalize_recording(c, *, pause_confirmed, game_exited=False, timeout=3):
    """Close after a confirmed pause or process exit; verify output completion.

    Deliberately independent of audit(): a storage/capture guard failure must
    not prevent cleanly stopping the recorder that the controller was using.
    """
    if not pause_confirmed and not game_exited:
        return {'finalized': False, 'reason': 'Game pause was not confirmed'}
    if not c.send('GetRecordStatus', raw=True)['outputActive']:
        return {'finalized': False, 'recording_active': False}
    result=c.send('StopRecord', raw=True)
    deadline=time.monotonic()+timeout
    while c.send('GetRecordStatus', raw=True)['outputActive']:
        if time.monotonic()>=deadline:
            raise RuntimeError('Recorder still active after StopRecord; finalization unverified')
        time.sleep(.1)
    path=pathlib.Path(result['outputPath'])
    if path.resolve().parent!=(ROOT/'recordings').resolve():
        raise RuntimeError('Recorder stopped but returned an unexpected output location')
    if not path.is_file() or path.stat().st_size<=0:
        raise RuntimeError('Recorder stopped but its output file is missing or empty')
    return {'finalized': True, 'recording_active': False,
            'path':str(path), 'bytes':path.stat().st_size}


def audit(c, *, require_recording=False):
    if any(c.send('GetSpecialInputs', raw=True).values()):
        raise RuntimeError('Global audio source exists: capture refused')
    collection = c.send('GetSceneCollectionList', raw=True)
    if collection['currentSceneCollectionName'] != 'L4D2 Game Only':
        raise RuntimeError('Wrong OBS scene collection')
    inputs = c.send('GetInputList', raw=True)['inputs']
    if len(inputs) != 1 or inputs[0]['inputName'] != NAME or inputs[0]['inputKind'] != 'game_capture':
        raise RuntimeError('Unexpected capture source')
    settings = c.send('GetInputSettings', {'inputName': NAME}, raw=True)['inputSettings']
    expected = {'capture_mode': 'window', 'window': TARGET, 'capture_audio': True,
                'capture_overlays': False, 'capture_cursor': False, 'priority': 0}
    for key, value in expected.items():
        if settings.get(key) != value:
            raise RuntimeError(f'Unsafe game-capture setting: {key}')
    filters=c.send('GetSourceFilterList',{'sourceName':NAME},raw=True)['filters']
    # User explicitly removed the obstructive box on September25. Preserve
    # process-only video/audio isolation; review public footage before upload.
    if filters:raise RuntimeError('Unexpected capture filter; user requested unobstructed game video')
    scene = c.send('GetSceneList', raw=True)
    if scene['currentProgramSceneName'] != 'Game only':
        raise RuntimeError('Wrong program scene')
    items = c.send('GetSceneItemList', {'sceneName': 'Game only'}, raw=True)['sceneItems']
    if len(items) != 1 or items[0]['sourceName'] != NAME or not items[0]['sceneItemEnabled']:
        raise RuntimeError('Unexpected scene contents')
    transform = items[0]['sceneItemTransform']
    if transform['sourceWidth'] != 1280 or transform['sourceHeight'] != 720:
        raise RuntimeError('Capture is missing or its dimensions changed')
    active = c.send('GetSourceActive', {'sourceName': NAME}, raw=True)
    if not active['videoActive'] or not active['videoShowing']:
        raise RuntimeError('Game video inactive')
    if c.send('GetInputMute', {'inputName': NAME}, raw=True)['inputMuted']:
        raise RuntimeError('Game audio muted')
    if c.send('GetInputAudioMonitorType', {'inputName': NAME}, raw=True)['monitorType'] != 'OBS_MONITORING_TYPE_NONE':
        raise RuntimeError('Unexpected audio monitoring loop')
    if c.send('GetStreamStatus', raw=True)['outputActive']:
        raise RuntimeError('Streaming is not authorized for this run')
    free = shutil.disk_usage(ROOT).free
    if free < 5 * 1024**3:
        raise RuntimeError('5 GiB free-space reserve reached')
    output = c.send('GetRecordStatus', raw=True)
    if require_recording and (not output['outputActive'] or output['outputPaused']):
        raise RuntimeError('Recording is not advancing')
    return {'capture_configuration_pass': True, 'audio': 'L4D2 process loopback only',
            'microphone_sources': 0, 'desktop_audio_sources': 0,
            'game_target': TARGET, 'free_bytes': free, 'recording': output,
            'note': 'Configuration audit alone does not replace the actual isolation test.'}


if __name__ == '__main__':
    print(json.dumps(audit(client()), indent=2))
