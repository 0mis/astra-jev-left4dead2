"""Recording preservation at controller handoff and guard failures."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from game_input import GameInput,game_process_exited
from obs_guard import ROOT,finalize_recording


def clock(seq,round_id='round-1'):
    return {'seq':seq,'t':seq/10,'map':'c1m4_atrium','round_id':round_id}


class PauseConfirmationTests(unittest.TestCase):
    def run_pause(self,rows,foreground=42):
        control=GameInput.__new__(GameInput);control.pid=42
        with patch('observe_game.read_json',side_effect=rows), \
             patch('game_input.time.sleep'), \
             patch('game_input.foreground_pid',return_value=foreground), \
             patch('game_input.send') as send:
            self.last_send=send
            result=control.emergency_pause()
        return result,send

    def test_advancing_clock_stops_after_pause_input(self):
        confirmed,send=self.run_pause([clock(10),clock(12),clock(14),clock(14)])
        self.assertTrue(confirmed)
        self.assertEqual(send.call_count,2)
        down,up=[call.args[0] for call in send.call_args_list]
        self.assertEqual((down.ki.scan,up.ki.scan),(67,67))
        self.assertEqual((down.ki.flags,up.ki.flags),(8,10))

    def test_stale_observer_cannot_confirm_pause_or_toggle_it(self):
        confirmed,send=self.run_pause([clock(10),clock(10)])
        self.assertFalse(confirmed);send.assert_not_called()

    def test_game_still_advancing_cannot_confirm_pause(self):
        confirmed,_=self.run_pause([clock(10),clock(12),clock(14),clock(16)])
        self.assertFalse(confirmed)

    def test_round_change_is_not_pause_confirmation(self):
        confirmed,_=self.run_pause([clock(10),clock(12),clock(14,'round-2'),clock(14,'round-2')])
        self.assertFalse(confirmed)

    def test_focus_loss_does_not_send_input(self):
        with self.assertRaisesRegex(RuntimeError,'lacks focus'):
            self.run_pause([clock(10),clock(12)],foreground=99)
        self.last_send.assert_not_called()


class ProcessExitTests(unittest.TestCase):
    def test_missing_process_is_exit_but_access_denied_is_unknown(self):
        with patch('game_input.K.OpenProcess',return_value=0),patch('game_input.C.get_last_error',return_value=87):
            self.assertTrue(game_process_exited(42))
        with patch('game_input.K.OpenProcess',return_value=0),patch('game_input.C.get_last_error',return_value=5):
            self.assertFalse(game_process_exited(42))

    def test_live_process_and_terminated_process_are_distinguished(self):
        for code,expected in ((259,False),(0,True)):
            def get_exit_code(handle,result):result._obj.value=code;return True
            with patch('game_input.K.OpenProcess',return_value=42), \
                 patch('game_input.K.GetExitCodeProcess',side_effect=get_exit_code), \
                 patch('game_input.K.CloseHandle') as close:
                self.assertEqual(game_process_exited(42),expected)
                close.assert_called_once_with(42)

    def test_failed_exit_query_does_not_claim_exit(self):
        with patch('game_input.K.OpenProcess',return_value=42), \
             patch('game_input.K.GetExitCodeProcess',return_value=False), \
             patch('game_input.K.CloseHandle') as close:
            self.assertFalse(game_process_exited(42));close.assert_called_once_with(42)


class RecordingFinalizationTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'private').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='capture-test-',dir=ROOT/'private')
        self.root=Path(self.temp.name).resolve()
        assert (ROOT/'private').resolve() in self.root.parents
        self.addCleanup(self.temp.cleanup)
        self.recordings=self.root/'recordings';self.recordings.mkdir()
        self.path=self.recordings/'test.mkv';self.path.write_bytes(b'preserved recording fixture')
        self.root_patch=patch('obs_guard.ROOT',self.root);self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_waits_for_actual_stop_and_verifies_saved_file(self):
        obs=Mock();obs.send.side_effect=[{'outputActive':True},
            {'outputPath':str(self.path)},{'outputActive':True},{'outputActive':False}]
        with patch('obs_guard.time.sleep'),patch('obs_guard.audit',side_effect=RuntimeError('low disk')):
            result=finalize_recording(obs,pause_confirmed=True)
        self.assertTrue(result['finalized']);self.assertFalse(result['recording_active'])
        self.assertEqual(result['bytes'],self.path.stat().st_size)
        self.assertEqual([call.args[0] for call in obs.send.call_args_list].count('StopRecord'),1)

    def test_uncertain_game_state_keeps_capture(self):
        obs=Mock();result=finalize_recording(obs,pause_confirmed=False)
        self.assertFalse(result['finalized']);obs.send.assert_not_called()

    def test_inactive_recorder_is_not_claimed_as_new_finalization(self):
        obs=Mock();obs.send.return_value={'outputActive':False}
        result=finalize_recording(obs,pause_confirmed=True)
        self.assertFalse(result['finalized']);self.assertEqual(obs.send.call_count,1)

    def test_confirmed_crash_finalizes_without_claiming_a_pause(self):
        obs=Mock();obs.send.side_effect=[{'outputActive':True},
            {'outputPath':str(self.path)},{'outputActive':False}]
        result=finalize_recording(obs,pause_confirmed=False,game_exited=True)
        self.assertTrue(result['finalized']);self.assertEqual(result['bytes'],self.path.stat().st_size)
        self.assertEqual([call.args[0] for call in obs.send.call_args_list].count('StopRecord'),1)

    def test_stop_timeout_is_not_reported_as_success(self):
        obs=Mock();obs.send.side_effect=[{'outputActive':True},
            {'outputPath':str(self.path)},{'outputActive':True}]
        with patch('obs_guard.time.monotonic',side_effect=[0,4]):
            with self.assertRaisesRegex(RuntimeError,'still active'):
                finalize_recording(obs,pause_confirmed=True)

    def test_empty_file_is_not_reported_as_saved(self):
        self.path.write_bytes(b'')
        obs=Mock();obs.send.side_effect=[{'outputActive':True},
            {'outputPath':str(self.path)},{'outputActive':False}]
        with self.assertRaisesRegex(RuntimeError,'missing or empty'):
            finalize_recording(obs,pause_confirmed=True)


if __name__=='__main__':unittest.main(verbosity=2)
