"""Prepare an isolated portable OBS profile, without starting capture."""
import configparser
import json
import pathlib
import secrets
import uuid

ROOT = pathlib.Path(__file__).resolve().parent
OBS = ROOT / 'tools' / 'obs-portable'
CFG = OBS / 'config' / 'obs-studio'


def write_ini(path, sections):
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read_dict(sections)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as output:
        config.write(output, space_around_delimiters=False)


def prepare():
    if (ROOT / 'obs-control.private.json').exists():
        raise RuntimeError('Existing OBS profile; refusing to overwrite live configuration')
    recordings = ROOT / 'recordings'
    recordings.mkdir(exist_ok=True)
    write_ini(CFG / 'global.ini', {
        'General': {'FirstRun': 'true', 'EnableAutoUpdates': 'false'},
        'Basic': {'Profile': 'L4D2 Game Only', 'ProfileDir': 'L4D2GameOnly',
                  'SceneCollection': 'L4D2 Game Only', 'SceneCollectionFile': 'L4D2GameOnly'},
        'BasicWindow': {'StudioMode': 'false', 'WarnBeforeStartingStream': 'true'},
    })
    write_ini(CFG / 'basic' / 'profiles' / 'L4D2GameOnly' / 'basic.ini', {
        'General': {'Name': 'L4D2 Game Only'},
        'Video': {'BaseCX': '1280', 'BaseCY': '720', 'OutputCX': '1280',
                  'OutputCY': '720', 'FPSType': '0', 'FPSCommon': '30'},
        'Audio': {'SampleRate': '48000', 'ChannelSetup': 'Stereo'},
        'Output': {'Mode': 'Simple', 'FilenameFormatting': 'L4D2-%CCYY-%MM-%DD-%hh-%mm-%ss'},
        'SimpleOutput': {'FilePath': recordings.as_posix(), 'RecFormat2': 'mkv',
                         'RecQuality': 'Stream', 'StreamEncoder': 'nvenc',
                         'RecEncoder': 'nvenc', 'VBitrate': '2500', 'ABitrate': '128'},
    })
    scenes = CFG / 'basic' / 'scenes'
    scenes.mkdir(parents=True, exist_ok=True)
    scene = {'name': 'L4D2 Game Only', 'current_scene': 'Game only',
             'current_program_scene': 'Game only', 'scene_order': [{'name': 'Game only'}],
             'sources': [{'name': 'Game only', 'uuid': str(uuid.uuid4()), 'id': 'scene',
                          'versioned_id': 'scene', 'settings': {'items': []},
                          'mixers': 0, 'volume': 1.0, 'enabled': True}],
             'groups': [], 'transitions': [], 'current_transition': 'Fade',
             'transition_duration': 300, 'quick_transitions': []}
    (scenes / 'L4D2GameOnly.json').write_text(json.dumps(scene, indent=2), encoding='utf-8')
    password = secrets.token_urlsafe(32)
    ws_dir = CFG / 'plugin_config' / 'obs-websocket'
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / 'config.json').write_text(json.dumps({
        'server_enabled': True, 'server_port': 4456, 'auth_required': True,
        'server_password': password, 'alerts_enabled': False,
    }, indent=2), encoding='utf-8')
    (ROOT / 'obs-control.private.json').write_text(json.dumps({
        'host': '127.0.0.1', 'port': 4456, 'password': password,
    }, indent=2), encoding='utf-8')
    print(json.dumps({'prepared': True, 'audio_sources': 0, 'video_sources': 0,
                      'recording_started': False, 'websocket_auth_required': True}))


if __name__ == '__main__':
    prepare()
