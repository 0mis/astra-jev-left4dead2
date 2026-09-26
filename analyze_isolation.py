"""Measure whether a known non-game audio probe leaked into a private recording."""
import argparse
import json
import pathlib
import subprocess
import wave
import numpy as np


def peak_gain(samples, probe):
    count = len(samples) + len(probe) - 1
    size = 1 << (count - 1).bit_length()
    cross = np.fft.irfft(np.fft.rfft(samples, size) * np.conj(np.fft.rfft(probe, size)), size)
    valid = cross[:len(samples) - len(probe) + 1]
    index = int(np.argmax(np.abs(valid)))
    return float(abs(valid[index]) / np.dot(probe, probe)), index


def main():
    p = argparse.ArgumentParser()
    p.add_argument('test_folder', type=pathlib.Path)
    p.add_argument('--ffmpeg', required=True)
    args = p.parse_args()
    report = json.loads((args.test_folder / 'capture-test.json').read_text())
    source = pathlib.Path(report['recording']['outputPath'])
    decoded = subprocess.run([args.ffmpeg, '-v', 'error', '-i', str(source), '-map', '0:a:0',
                              '-ac', '1', '-ar', '48000', '-f', 'f32le', '-'],
                             check=True, capture_output=True).stdout
    samples = np.frombuffer(decoded, dtype='<f4').astype(np.float64)
    with wave.open(str(args.test_folder / 'external-probe.wav'), 'rb') as wav:
        probe = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').reshape(-1, 2).mean(axis=1) / 32768
    gain, peak = peak_gain(samples, probe)
    # A synthetic positive control proves the detector flags an actual leak.
    control = samples.copy()
    control[48000:48000 + len(probe)] += probe
    control_gain, _ = peak_gain(control, probe)
    rms = float(np.sqrt(np.mean(samples**2)))
    result = {'decoded_audio_seconds': len(samples) / 48000, 'audio_rms': rms,
              'audio_peak': float(np.max(np.abs(samples))), 'external_probe_peak_gain': gain,
              'maximum_probe_match_seconds': peak / 48000, 'positive_control_gain': control_gain,
              'external_probe_excluded': gain < .025, 'game_audio_present': rms > 1e-4,
              'detector_positive_control_pass': control_gain > .8,
              'visual_review': 'pending', 'full_voice_listening_test_claimed': False}
    result['audio_isolation_test_pass'] = all(result[k] for k in (
        'external_probe_excluded', 'game_audio_present', 'detector_positive_control_pass'))
    (args.test_folder / 'audio-analysis.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    subprocess.run([args.ffmpeg, '-v', 'error', '-i', str(source), '-an',
                    '-vf', 'fps=1,scale=320:180,tile=5x3', '-frames:v', '1',
                    str(args.test_folder / 'private-test-contact-sheet.jpg')], check=True)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
